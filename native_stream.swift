import Foundation
import AppKit
import ScreenCaptureKit
import CoreGraphics
import CoreMedia
import ImageIO
import UniformTypeIdentifiers
import CryptoKit

// This process never exports a pixel until the native window masks are drawn.
// CGWindowList is sampled metadata, not an atomic compositor privacy guarantee.
private struct Policy: Decodable {
    var private_apps: [String]?
    var private_titles: [String]?
    var apps: [String] {
        (["telegram", "1password", "lastpass", "challenger", "instagram", "gmail", "passwords"] + (private_apps ?? [])).map { $0.lowercased() }
    }
    var titles: [String] {
        (["telegram", "1password", "lastpass", "challenger", "instagram", "gmail", "passwords", "private key", "secret key", "otp", "one-time", "verification code", "login", "log in", "sign in", "inbox", "verification", "2fa", "security key", "api key", "vault", "recovery phrase"] + (private_titles ?? [])).map { $0.lowercased() }
    }
    func privateApp(_ owner: String, _ bundle: String) -> Bool {
        let value = (owner + " " + bundle).lowercased()
        return owner.lowercased() == "obs" || bundle.lowercased().hasPrefix("com.obsproject") || apps.contains { !$0.isEmpty && value.contains($0) }
    }
}

private struct Snapshot {
    let time: Double
    let windows: [Int: CGRect]
    let valid: Bool
    let epoch: Int
    let geometryEpoch: Int
    let visible: [[String: Int]]
    var scanMS: Double = 0
}

// An idle compositor event refreshes proof only for the exact cached pixel subject.
private func idleReuseIsBound(_ original: Snapshot?, _ current: Snapshot) -> Bool {
    guard let original, original.valid, current.valid else { return false }
    return original.epoch == current.epoch && original.geometryEpoch == current.geometryEpoch &&
        original.windows == current.windows && original.visible == current.visible
}

private func fail() -> Never {
    FileHandle.standardError.write(Data("Local display stream failed\n".utf8))
    exit(1)
}

private func rectangles(_ masks: [CGRect]) -> [[String: Int]] {
    masks.map { ["x": Int($0.minX), "y": Int($0.minY), "w": Int($0.width), "h": Int($0.height)] }
}

private func publish(_ image: CGImage, masks: [CGRect], capture: Double, sequence: Int,
                     closed: Bool, transition: Bool, age: Double, epoch: Int = 0, idle: Bool = false, windows: [[String: Int]] = [], scanMS: Double = 0, raw: Bool = false) {
    let encodeStart = ProcessInfo.processInfo.systemUptime
    guard let context = CGContext(data: nil, width: image.width, height: image.height,
                                  bitsPerComponent: 8, bytesPerRow: image.width * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue) else { fail() }
    context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    context.setFillColor(red: 14.0 / 255, green: 17.0 / 255, blue: 20.0 / 255, alpha: 1)
    // CGWindow bounds use top-left coordinates; CGContext has bottom-left origin.
    for rect in masks {
        context.fill(CGRect(x: rect.minX, y: CGFloat(image.height) - rect.maxY,
                            width: rect.width, height: rect.height))
    }
    var payload: Data
    if raw {
        guard let pixels = context.data else { fail() }
        payload = Data(bytes: pixels, count: image.width * image.height * 4)
    } else {
        guard let safe = context.makeImage(), let data = CFDataCreateMutable(nil, 0),
              let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { fail() }
        CGImageDestinationAddImage(destination, safe,
            [kCGImagePropertyPNGDictionary: [kCGImagePropertyPNGCompressionFilter: IMAGEIO_PNG_FILTER_SUB]] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { fail() }
        payload = data as Data
    }
    let metadata: [String: Any] = ["capture_monotonic": capture, "pixel_format": raw ? "rgbx" : "png", "snapshots_ms": scanMS,
        "encode_ms": (ProcessInfo.processInfo.systemUptime - encodeStart) * 1000,
        "emitted_monotonic": ProcessInfo.processInfo.systemUptime, "sequence": sequence,
        "width": image.width, "height": image.height, "masks": rectangles(masks), "windows": windows,
        "private_window_count": masks.count, "fail_closed": closed,
        "transition_guard": transition, "scene_epoch": epoch, "idle_replay": idle, "metadata_age_ms": max(0, age * 1000)]
    guard let json = try? JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys]) else { fail() }
    guard json.count <= Int(UInt32.max), payload.count <= Int(UInt32.max) else { fail() }
    var output = Data()
    var length = UInt32(json.count).bigEndian
    withUnsafeBytes(of: &length) { output.append(contentsOf: $0) }
    output.append(json)
    length = UInt32(payload.count).bigEndian
    withUnsafeBytes(of: &length) { output.append(contentsOf: $0) }
    output.append(payload)
    do { try FileHandle.standardOutput.write(contentsOf: output) } catch { exit(0) }
}

private final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    let policy: Policy
    let display: CGRect
    let raw: Bool
    let queue = DispatchQueue(label: "safestream.native.frames")
    let metadataQueue = DispatchQueue(label: "safestream.native.metadata")
    var bundles: [Int: (owner: String, bundle: String)] = [:]
    var history: [Snapshot] = []
    var timer: DispatchSourceTimer?
    var sequence = 0
    var epoch = 0
    var geometryEpoch = 0
    var topology: [Int: CGRect] = [:]
    var sensitiveIDs: Set<Int> = []
    var identityDigest: Data?
    var lastImage: CGImage?
    var lastImageCapture: Double?
    var lastImageBinding: Snapshot?
    init(policy: Policy, display: CGRect, raw: Bool = false) { self.policy = policy; self.display = display; self.raw = raw }

    func snapshot() -> Snapshot {
        let scanStart = ProcessInfo.processInfo.systemUptime
        guard let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]], !rows.isEmpty else {
            return Snapshot(time: ProcessInfo.processInfo.systemUptime, windows: [:], valid: false, epoch: epoch, geometryEpoch: geometryEpoch, visible: [])
        }
        var masks: [Int: CGRect] = [:]
        var seenPIDs: Set<Int> = []
        var all: [Int: CGRect] = [:]
        var visible: [[String: Int]] = []
        var identities: [String] = []
        for row in rows {
            guard let number = row[kCGWindowNumber as String] as? Int,
                  let pid = row[kCGWindowOwnerPID as String] as? Int,
                  let owner = row[kCGWindowOwnerName as String] as? String,
                  let bounds = row[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
                  rect.minX.isFinite, rect.minY.isFinite, rect.width.isFinite, rect.height.isFinite else {
                return Snapshot(time: ProcessInfo.processInfo.systemUptime, windows: [:], valid: false, epoch: epoch, geometryEpoch: geometryEpoch, visible: [])
            }
            let title = row[kCGWindowName as String] as? String
            seenPIDs.insert(pid)
            if bundles[pid]?.owner != owner {
                bundles[pid] = (owner, NSRunningApplication(processIdentifier: pid_t(pid))?.bundleIdentifier ?? "")
            }
            let bundle = bundles[pid]?.bundle ?? ""
            // An unavailable title cannot establish that a window is public.
            let sensitive = policy.privateApp(owner, bundle) || title == nil || policy.titles.contains {
                !$0.isEmpty && (title ?? "").lowercased().contains($0)
            }
            if rect.intersects(display) {
                all[number] = rect
                var numeric = rectangles([rect.offsetBy(dx: -display.minX, dy: -display.minY).integral.intersection(CGRect(origin: .zero, size: display.size))])[0]
                numeric["id"] = number
                visible.append(numeric)
                identities.append("\(number):\(owner):\(bundle):\(title ?? "")")
            }
            if sensitive && rect.intersects(display) { masks[number] = rect }
        }
        bundles = bundles.filter { seenPIDs.contains($0.key) }
        let digest = Data(SHA256.hash(data: Data(identities.joined(separator: "\n").utf8)))
        if all != topology { geometryEpoch += 1 }
        if Set(masks.keys) != sensitiveIDs || digest != identityDigest { epoch += 1 }
        identityDigest = digest
        topology = all; sensitiveIDs = Set(masks.keys)
        var result = Snapshot(time: ProcessInfo.processInfo.systemUptime, windows: masks, valid: true, epoch: epoch, geometryEpoch: geometryEpoch, visible: visible)
        result.scanMS = (result.time - scanStart) * 1000
        return result
    }

    func startMetadata() {
        history = [snapshot()]
        let timer = DispatchSource.makeTimerSource(queue: metadataQueue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(33))
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            self.history.append(self.snapshot())
            let cutoff = ProcessInfo.processInfo.systemUptime - 2
            while self.history.count > 2 && self.history[1].time < cutoff { self.history.removeFirst() }
        }
        self.timer = timer
        timer.resume()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) { fail() }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sample.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let info = attachments.first, let status = info[.status] as? Int,
              status == SCFrameStatus.complete.rawValue || status == SCFrameStatus.idle.rawValue else { return }
        let idle = status == SCFrameStatus.idle.rawValue
        let sourceTime = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
        // Never spend encoding time on a queued frame already outside freshness.
        if !idle && (!sourceTime.isFinite || ProcessInfo.processInfo.systemUptime - sourceTime > 0.5) { return }
        let (after, sampledHistory) = metadataQueue.sync { () -> (Snapshot, [Snapshot]) in
            let latest = history.last
            let after = !idle && (latest?.time ?? 0) >= sourceTime ? latest! : snapshot()
            let previous = history
            history.append(after)
            return (after, previous)
        }
        let idleBound = idleReuseIsBound(lastImageBinding, after)
        let timestamp = idle ? (idleBound ? after.time : (lastImageCapture ?? after.time)) :
            sourceTime
        let before = sampledHistory.last { $0.time <= timestamp }
        let relevant = sampledHistory.filter { $0.time >= (before?.time ?? after.time) } + [after]
        var united: [Int: CGRect] = [:]
        for state in relevant {
            for (id, rect) in state.windows { united[id] = united[id].map { $0.union(rect) } ?? rect }
        }
        // A new private window/title has no trustworthy earlier location.
        let transition = before.map { Set(united.keys).subtracting($0.windows.keys).count > 0 } ?? true
        let age = before.map { timestamp - $0.time } ?? 2
        let closed = (idle && !idleBound) || before == nil || !timestamp.isFinite || timestamp > after.time + 0.1 ||
            after.time - timestamp > 0.5 || age > 0.15 || relevant.contains { !$0.valid } || transition
        var current: CGImage?
        if idle { current = lastImage }
        else if let buffer = CMSampleBufferGetImageBuffer(sample) {
            CVPixelBufferLockBaseAddress(buffer, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
            if let base = CVPixelBufferGetBaseAddress(buffer) {
                let bytes = Data(bytes: base, count: CVPixelBufferGetBytesPerRow(buffer) * CVPixelBufferGetHeight(buffer))
                if let provider = CGDataProvider(data: bytes as CFData) {
                    current = CGImage(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer),
                        bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue:
                            CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue),
                        provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
                }
            }
        }
        guard let cg = current,
              cg.width == Int(display.width), cg.height == Int(display.height) else { return }
        if !idle {
            lastImage = cg
            lastImageCapture = timestamp
            // Metadata must cover an unchanged geometry/identity at the source time.
            lastImageBinding = !closed && before?.epoch == after.epoch &&
                before?.geometryEpoch == after.geometryEpoch ? after : nil
        }
        let frame = CGRect(x: 0, y: 0, width: display.width, height: display.height)
        let masks = closed ? [frame] : united.values.compactMap { rect -> CGRect? in
            let local = rect.insetBy(dx: -24, dy: -24).offsetBy(dx: -display.minX, dy: -display.minY).integral.intersection(frame)
            return local.isNull || local.isEmpty ? nil : local
        }
        sequence += 1
        publish(cg, masks: masks, capture: timestamp.isFinite ? timestamp : after.time,
                sequence: sequence, closed: closed, transition: transition, age: age, epoch: after.epoch, idle: idle, windows: after.visible, scanMS: after.scanMS, raw: raw)
    }
}

@main
struct NativeStream {
    static func main() async {
        let args = CommandLine.arguments
        let idleTest = args.count == 3 && args[1] == "--self-test-idle" && ["same", "geometry", "identity", "missing"].contains(args[2])
        let rawTest = args.count == 2 && args[1] == "--self-test-raw"
        if (args.count == 2 && args[1] == "--self-test") || idleTest || rawTest {
            guard let context = CGContext(data: nil, width: 128, height: 96, bitsPerComponent: 8,
                                          bytesPerRow: 512, space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { fail() }
            context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            context.fill(CGRect(x: 0, y: 0, width: 128, height: 96))
            guard let image = context.makeImage() else { fail() }
            let rect = CGRect(x: 7, y: 11, width: 31, height: 23)
            var masks = [rect]
            var closed = false
            var timestamp = ProcessInfo.processInfo.systemUptime
            if idleTest {
                let original = Snapshot(time: 10, windows: [1: rect], valid: true, epoch: 1, geometryEpoch: 1, visible: [])
                let current = Snapshot(time: 20, windows: [1: rect], valid: true,
                    epoch: args[2] == "identity" ? 2 : 1,
                    geometryEpoch: args[2] == "geometry" ? 2 : 1, visible: [])
                closed = !idleReuseIsBound(args[2] == "missing" ? nil : original, current)
                timestamp = closed ? original.time : current.time
                if closed { masks = [CGRect(x: 0, y: 0, width: 128, height: 96)] }
            }
            publish(image, masks: masks, capture: timestamp, sequence: 1, closed: closed,
                    transition: false, age: 0, idle: idleTest, raw: rawTest)
            return
        }
        let raw = args.last == "--raw"
        let count = args.count - (raw ? 1 : 0)
        guard count == 6 || count == 7 else { fail() }
        let values = args[1...4].compactMap(Int.init)
        guard values.count == 4, values[2] > 0, values[3] > 0,
              values[2] <= 32768, values[3] <= 32768,
              let fps = Int(count == 7 ? args[6] : "15"), fps > 0, fps <= 30,
              let data = try? Data(contentsOf: URL(fileURLWithPath: args[5])),
              let policy = try? JSONDecoder().decode(Policy.self, from: data) else { fail() }
        let requested = CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let matches = content.displays.filter { CGDisplayBounds($0.displayID) == requested }
            guard matches.count == 1, let display = matches.first else { fail() }
            let excluded = content.applications.filter { policy.privateApp($0.applicationName, $0.bundleIdentifier) }
            let filter = SCContentFilter(display: display, excludingApplications: excluded, exceptingWindows: [])
            let config = SCStreamConfiguration()
            config.width = values[2]; config.height = values[3]
            config.pixelFormat = kCVPixelFormatType_32BGRA
            config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
            config.queueDepth = 3; config.showsCursor = true
            let capture = Capture(policy: policy, display: requested, raw: raw)
            capture.metadataQueue.sync { capture.startMetadata() }
            let stream = SCStream(filter: filter, configuration: config, delegate: capture)
            try stream.addStreamOutput(capture, type: .screen, sampleHandlerQueue: capture.queue)
            try await stream.startCapture()
            // Retain the stream and callback owner for the entire worker lifetime.
            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                withExtendedLifetime((stream, capture)) {}
            }
        } catch { fail() }
    }
}
