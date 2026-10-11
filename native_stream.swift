import Foundation
import Darwin
import AppKit
@preconcurrency import ScreenCaptureKit
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
    var masks: [CGRect] = []
    var excludedIDs: [Int] = []
    var pids: [Int: Int] = [:]
    var foreground: [Int: [CGRect]] = [:]
}

private func exclusionIsBound(applied: Double?, source: Double, matches: Bool) -> Bool {
    guard let applied else { return false }
    return applied.isFinite && source.isFinite && source >= applied && matches
}

private struct FilterReceipt: @unchecked Sendable {
    let filter: SCContentFilter
    let applied: Double
    let ids: Set<Int>
    let appPIDs: Set<Int>
    func excludes(_ id: Int, pid: Int, source: Double) -> Bool {
        exclusionIsBound(applied: applied, source: source, matches: ids.contains(id) || appPIDs.contains(pid))
    }
}

private func controlIDs(_ data: Data) -> Set<Int>? {
    guard data.count <= 65536,
          let array = try? JSONSerialization.jsonObject(with: data) as? [Any], array.count <= 4096 else { return nil }
    var ids: Set<Int> = []
    for value in array {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue > 0,
              number.doubleValue <= Double(UInt32.max), number.doubleValue.rounded() == number.doubleValue else { return nil }
        ids.insert(number.intValue)
    }
    return ids
}

// An idle compositor event refreshes proof only for the exact cached pixel subject.
private func idleReuseIsBound(_ original: Snapshot?, _ current: Snapshot) -> Bool {
    guard let original, original.valid, current.valid else { return false }
    return original.epoch == current.epoch && original.geometryEpoch == current.geometryEpoch &&
        original.windows == current.windows && original.visible == current.visible && original.masks == current.masks
}

private func fail() -> Never {
    FileHandle.standardError.write(Data("Local display stream failed\n".utf8))
    exit(1)
}

// Foreground geometry may clip a mask only after compositor exclusion is proved.
private func subtract(_ rect: CGRect, _ foreground: CGRect) -> [CGRect] {
    let cut = rect.intersection(foreground)
    if cut.isNull || cut.isEmpty { return [rect] }
    return [CGRect(x: rect.minX, y: rect.minY, width: rect.width, height: cut.minY - rect.minY),
        CGRect(x: rect.minX, y: cut.maxY, width: rect.width, height: rect.maxY - cut.maxY),
        CGRect(x: rect.minX, y: cut.minY, width: cut.minX - rect.minX, height: cut.height),
        CGRect(x: cut.maxX, y: cut.minY, width: rect.maxX - cut.maxX, height: cut.height)].filter { !$0.isEmpty }
}

private func privateMasks(_ rect: CGRect, foreground: [CGRect], excluded: Bool) -> [CGRect] {
    var pieces = [rect.insetBy(dx: -24, dy: -24)]
    if excluded {
        for rect in foreground { pieces = pieces.flatMap { subtract($0, rect) } }
    }
    return pieces
}

private func rectangles(_ masks: [CGRect]) -> [[String: Int]] {
    masks.map { ["x": Int($0.minX), "y": Int($0.minY), "w": Int($0.width), "h": Int($0.height)] }
}

private func image(from buffer: CVPixelBuffer) -> CGImage? {
    guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_32BGRA else { return nil }
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
    guard let base = CVPixelBufferGetBaseAddress(buffer) else { return nil }
    let bytes = Data(bytes: base, count: CVPixelBufferGetBytesPerRow(buffer) * CVPixelBufferGetHeight(buffer))
    guard let provider = CGDataProvider(data: bytes as CFData) else { return nil }
    return CGImage(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer),
        bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue:
            CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue),
        provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
}

// The SDK callback transfers a completed, read-only buffer across executors.
private struct CapturedSample: @unchecked Sendable { let value: CMSampleBuffer }
@MainActor private func screenshotSample(filter: SCContentFilter, configuration: SCStreamConfiguration) async throws -> CapturedSample {
    try await withCheckedThrowingContinuation { continuation in
        SCScreenshotManager.captureSampleBuffer(contentFilter: filter, configuration: configuration) { sample, error in
            if let error { continuation.resume(throwing: error) }
            else if let sample { continuation.resume(returning: CapturedSample(value: sample)) }
            else { continuation.resume(throwing: NSError(domain: "LocalCapture", code: -1)) }
        }
    }
}

private func publish(_ image: CGImage, masks: [CGRect], capture: Double, sequence: Int,
                     closed: Bool, transition: Bool, age: Double, epoch: Int = 0, idle: Bool = false, windows: [[String: Int]] = [], scanMS: Double = 0, raw: Bool = false, heartbeat: Bool = false, errorCode: Int = 0, started: Double? = nil, excludedIDs: [Int] = []) {
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
    let metadata: [String: Any] = ["capture_monotonic": capture, "capture_started_monotonic": started ?? capture, "screenshot_heartbeat": heartbeat, "capture_error_code": errorCode, "pixel_format": raw ? "rgbx" : "png", "snapshots_ms": scanMS,
        "encode_ms": (ProcessInfo.processInfo.systemUptime - encodeStart) * 1000,
        "emitted_monotonic": ProcessInfo.processInfo.systemUptime, "sequence": sequence,
        "width": image.width, "height": image.height, "excluded_window_ids": excludedIDs, "masks": rectangles(masks), "windows": windows,
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

private final class Capture: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    // Frame state belongs to queue; metadata state belongs to metadataQueue.
    let policy: Policy
    let display: CGRect
    let raw: Bool
    var filter: SCContentFilter
    let excludedAppPIDs: Set<Int>
    var receipts: [FilterReceipt] = []
    var stickyIDs: Set<Int> = [] // metadataQueue owner; controls are monotonic.
    var filterTimer: DispatchSourceTimer?
    var filterInFlight = false
    var filterRequestedIDs: Set<Int> = []
    let quietStreamForTest = ProcessInfo.processInfo.environment["SAFESTREAM_NATIVE_TEST_QUIET_STREAM"] == "1"
    let queue = DispatchQueue(label: "safestream.native.frames")
    let metadataQueue = DispatchQueue(label: "safestream.native.metadata")
    var bundles: [Int: (owner: String, bundle: String)] = [:]
    var history: [Snapshot] = []
    var timer: DispatchSourceTimer?
    var heartbeatTimer: DispatchSourceTimer?
    var heartbeatInFlight = false
    var lastCompleteAt = ProcessInfo.processInfo.systemUptime
    var lastHeartbeatAt: Double = 0
    var lastCapturedPrivateWindows: [Int: CGRect] = [:]
    var sequence = 0
    var epoch = 0
    var geometryEpoch = 0
    var topology: [Int: CGRect] = [:]
    var sensitiveIDs: Set<Int> = []
    var identityDigest: Data?
    var lastImage: CGImage?
    var lastImageCapture: Double?
    var lastImageFilterApplied: Double?
    var lastImageBinding: Snapshot?
    init(policy: Policy, display: CGRect, filter: SCContentFilter, excludedAppPIDs: Set<Int>, raw: Bool = false) {
        self.policy = policy; self.display = display; self.filter = filter; self.raw = raw
        self.excludedAppPIDs = excludedAppPIDs
        self.receipts = [FilterReceipt(filter: filter, applied: 0, ids: [], appPIDs: excludedAppPIDs)]
    }

    func snapshot() -> Snapshot {
        let scanStart = ProcessInfo.processInfo.systemUptime
        guard let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]], !rows.isEmpty else {
            return Snapshot(time: ProcessInfo.processInfo.systemUptime, windows: [:], valid: false, epoch: epoch, geometryEpoch: geometryEpoch, visible: [])
        }
        var masks: [Int: CGRect] = [:]
        var pids: [Int: Int] = [:]
        var foreground: [Int: [CGRect]] = [:]
        var foregroundRects: [CGRect] = []
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
            let sensitive = stickyIDs.contains(number) || policy.privateApp(owner, bundle) || title == nil || policy.titles.contains {
                !$0.isEmpty && (title ?? "").lowercased().contains($0)
            }
            if rect.intersects(display) {
                all[number] = rect
                var numeric = rectangles([rect.offsetBy(dx: -display.minX, dy: -display.minY).integral.intersection(CGRect(origin: .zero, size: display.size))])[0]
                numeric["id"] = number
                visible.append(numeric)
                identities.append("\(number):\(owner):\(bundle):\(title ?? "")")
            }
            if sensitive && rect.intersects(display) {
                masks[number] = rect
                pids[number] = pid
                foreground[number] = foregroundRects
            }
            // Floating/transparent foregrounds are safe here: privateMasks only
            // clips when that source already excludes the private background.
            if (row[kCGWindowAlpha as String] as? Double ?? 0) > 0 {
                foregroundRects.append(rect)
            }
        }
        bundles = bundles.filter { seenPIDs.contains($0.key) }
        let digest = Data(SHA256.hash(data: Data(identities.joined(separator: "\n").utf8)))
        if all != topology { geometryEpoch += 1 }
        if Set(masks.keys) != sensitiveIDs || digest != identityDigest { epoch += 1 }
        identityDigest = digest
        topology = all; sensitiveIDs = Set(masks.keys)
        var result = Snapshot(time: ProcessInfo.processInfo.systemUptime, windows: masks, valid: true, epoch: epoch, geometryEpoch: geometryEpoch, visible: visible)
        result.scanMS = (result.time - scanStart) * 1000
        result.masks = masks.values.map { $0.insetBy(dx: -24, dy: -24) }
        result.pids = pids; result.foreground = foreground
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
        guard !quietStreamForTest, type == .screen, sample.isValid,
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
        let idleBound = idleReuseIsBound(lastImageBinding, after) &&
            lastImageFilterApplied == receipts.last?.applied
        let timestamp = idle ? (idleBound ? after.time : (lastImageCapture ?? after.time)) :
            sourceTime
        var current: CGImage?
        if idle { current = lastImage }
        else if let buffer = CMSampleBufferGetImageBuffer(sample) {
            current = image(from: buffer)
        }
        guard let cg = current,
              cg.width == Int(display.width), cg.height == Int(display.height) else { return }
        if !idle { lastCompleteAt = ProcessInfo.processInfo.systemUptime }
        emitImage(cg, timestamp: timestamp, after: after, sampledHistory: sampledHistory, idle: idle)
    }

    func emitImage(_ cg: CGImage, timestamp: Double, after: Snapshot, sampledHistory: [Snapshot],
                   idle: Bool = false, heartbeat: Bool = false, started: Double? = nil, receipt: FilterReceipt? = nil) {
        guard cg.width == Int(display.width), cg.height == Int(display.height) else { return }
        let idleBound = idleReuseIsBound(lastImageBinding, after) &&
            lastImageFilterApplied == receipts.last?.applied
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
        if !idle {
            lastImage = cg
            lastCapturedPrivateWindows = after.windows
            lastImageCapture = timestamp
            lastImageFilterApplied = (receipt ?? receipts.last { $0.applied <= timestamp })?.applied
            // Metadata must cover an unchanged geometry/identity at the source time.
            lastImageBinding = !closed && before?.epoch == after.epoch &&
                before?.geometryEpoch == after.geometryEpoch ? after : nil
        }
        let frame = CGRect(x: 0, y: 0, width: display.width, height: display.height)
        // Bind clipping permissions to the actual source, never the latest update.
        let proof = receipt ?? receipts.last { $0.applied <= timestamp }
        let excluded = after.windows.keys.filter {
            proof?.excludes($0, pid: after.pids[$0] ?? -1, source: timestamp) == true
        }.sorted()
        let masks = closed ? [frame] : relevant.flatMap { state in
            state.windows.flatMap { id, rect in
                privateMasks(rect, foreground: state.foreground[id] ?? [],
                    excluded: proof?.excludes(id, pid: state.pids[id] ?? -1, source: timestamp) == true)
            }
        }.compactMap { rect -> CGRect? in
            let local = rect.offsetBy(dx: -display.minX, dy: -display.minY).integral.intersection(frame)
            return local.isNull || local.isEmpty ? nil : local
        }
        sequence += 1
        publish(cg, masks: masks, capture: timestamp.isFinite ? timestamp : after.time,
                sequence: sequence, closed: closed, transition: transition, age: age, epoch: after.epoch, idle: idle, windows: after.visible, scanMS: after.scanMS, raw: raw, heartbeat: heartbeat, started: started, excludedIDs: closed ? [] : excluded)
    }

    func startControls(stream: SCStream, selectedDisplay: SCDisplay) {
        // A bounded, numeric-only channel. Malformed controls retain every mask.
        DispatchQueue.global(qos: .utility).async { [weak self] in
            var line = Data(); var oversized = false
            var buffer = [UInt8](repeating: 0, count: 4096)
            while true {
                let count = Darwin.read(STDIN_FILENO, &buffer, buffer.count)
                if count <= 0 { break }
                for byte in buffer.prefix(count) {
                    if byte == 10 {
                        if !oversized, let ids = controlIDs(line), let self {
                            self.queue.async {
                                let admitted = self.metadataQueue.sync { () -> Bool in
                                    let union = self.stickyIDs.union(ids)
                                    guard union.count <= 4096 else { return false }
                                    self.stickyIDs = union; return true
                                }
                                guard admitted else {
                                    FileHandle.standardError.write(Data("Local capture control refused\n".utf8)); return
                                }
                                self.refreshFilter(stream: stream, selectedDisplay: selectedDisplay, force: true)
                            }
                        } else { FileHandle.standardError.write(Data("Local capture control refused\n".utf8)) }
                        line.removeAll(keepingCapacity: true); oversized = false
                    } else if line.count < 65536 { line.append(byte) }
                    else { oversized = true }
                }
            }
        }
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in self?.refreshFilter(stream: stream, selectedDisplay: selectedDisplay) }
        filterTimer = timer; timer.resume()
    }

    func refreshFilter(stream: SCStream, selectedDisplay: SCDisplay, force: Bool = false) {
        guard !filterInFlight else { return }
        let wanted = metadataQueue.sync {
            let visible = Set(history.last?.visible.compactMap { $0["id"] } ?? [])
            return Set(history.last?.windows.keys.map { $0 } ?? []).union(stickyIDs.intersection(visible))
        }
        guard force || wanted != filterRequestedIDs else { return }
        filterInFlight = true
        let priorReceipt = receipts.last
        Task { @MainActor in
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                let excluded = content.windows.filter { window in
                    wanted.contains(Int(window.windowID)) || self.policy.privateApp(
                        window.owningApplication?.applicationName ?? "", window.owningApplication?.bundleIdentifier ?? "") ||
                        self.policy.titles.contains { !$0.isEmpty && (window.title ?? "").lowercased().contains($0) }
                }
                let ids = Set(excluded.map { Int($0.windowID) })
                // Unshareable CG surfaces must not repeatedly reapply an
                // identical filter and invalidate otherwise fresh idle frames.
                if let priorReceipt, priorReceipt.appPIDs.isEmpty, priorReceipt.ids == ids {
                    self.queue.async {
                        self.filterRequestedIDs = wanted.intersection(ids)
                        self.filterInFlight = false
                    }
                    return
                }
                let next = SCContentFilter(display: selectedDisplay, excludingWindows: excluded)
                try await stream.updateContentFilter(next)
                // A source older than this successful application has no permission.
                let applied = ProcessInfo.processInfo.systemUptime
                self.queue.async {
                    self.filter = next
                    self.receipts.append(FilterReceipt(filter: next, applied: applied, ids: ids, appPIDs: []))
                    let cutoff = ProcessInfo.processInfo.systemUptime - 3
                    while self.receipts.count > 2 && self.receipts[1].applied < cutoff { self.receipts.removeFirst() }
                    self.filterRequestedIDs = wanted.intersection(ids)
                    self.filterInFlight = false
                }
            } catch { self.queue.async { self.filterInFlight = false } }
        }
    }

    func startHeartbeat() {
        // Screenshot control matches the existing one-shot helper's accepted options.
        let configuration = SCStreamConfiguration()
        configuration.width = Int(display.width); configuration.height = Int(display.height)
        configuration.showsCursor = true
        configuration.pixelFormat = kCVPixelFormatType_32BGRA
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + .milliseconds(500), repeating: .milliseconds(67))
        timer.setEventHandler { [weak self] in
            guard let self, !self.heartbeatInFlight else { return }
            let now = ProcessInfo.processInfo.systemUptime
            let currentPrivate = self.metadataQueue.sync { self.history.last?.windows ?? [:] }
            let moved = currentPrivate != self.lastCapturedPrivateWindows
            guard now - self.lastHeartbeatAt >= (moved ? 1.0 / 15 : 0.5),
                  moved || now - self.lastCompleteAt >= 0.5 else { return }
            self.heartbeatInFlight = true
            self.lastHeartbeatAt = ProcessInfo.processInfo.systemUptime
            let receipt = self.receipts.last!
            Task { @MainActor in
                // Read metadata BEFORE the real screenshot; never re-date a cached image.
                let before = self.metadataQueue.sync { self.snapshot() }
                let started = ProcessInfo.processInfo.systemUptime
                do {
                    let sample = try await screenshotSample(filter: receipt.filter, configuration: configuration).value
                    let source = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
                    guard sample.isValid, CMSampleBufferDataIsReady(sample), CMSampleBufferGetNumSamples(sample) == 1,
                          source.isFinite, source >= started,
                          source <= ProcessInfo.processInfo.systemUptime,
                          let buffer = CMSampleBufferGetImageBuffer(sample),
                          CVPixelBufferGetWidth(buffer) == Int(self.display.width),
                          CVPixelBufferGetHeight(buffer) == Int(self.display.height),
                          let image = image(from: buffer) else { throw NSError(domain: "LocalCapture", code: -1) }
                    // Screenshot buffers have no SCStream status attachment on this SDK.
                    // Data-ready, single-image readiness is their supported complete contract.
                    let info = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]]
                    if let status = info?.first?[.status] as? Int, status != SCFrameStatus.complete.rawValue {
                        throw NSError(domain: "LocalCapture", code: -2)
                    }
                    let (after, history) = self.metadataQueue.sync { () -> (Snapshot, [Snapshot]) in
                        let after = self.snapshot()
                        let history = self.history + [before]
                        self.history.append(after)
                        return (after, history.sorted { $0.time < $1.time })
                    }
                    self.queue.async {
                        self.heartbeatInFlight = false
                        // A newer streamed frame already satisfies this capture interval.
                        guard self.lastCompleteAt <= started else { return }
                        self.emitImage(image, timestamp: source, after: after,
                                       sampledHistory: history, heartbeat: true, started: started, receipt: receipt)
                    }
                } catch {
                    // Failed requests export only a fully masked slate and a numeric code.
                    let code = (error as NSError).code
                    self.queue.async {
                        self.heartbeatInFlight = false
                        guard let context = CGContext(data: nil, width: Int(self.display.width), height: Int(self.display.height),
                            bitsPerComponent: 8, bytesPerRow: Int(self.display.width) * 4,
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue),
                            let slate = context.makeImage() else { return }
                        self.sequence += 1
                        publish(slate, masks: [CGRect(origin: .zero, size: self.display.size)], capture: started,
                            sequence: self.sequence, closed: true, transition: false, age: 0,
                            raw: self.raw, heartbeat: true, errorCode: code, started: started)
                    }
                }
            }
        }
        heartbeatTimer = timer
        timer.resume()

    }
}

@main
struct NativeStream {
    static func main() async {
        let args = CommandLine.arguments
        if args.count == 2 && args[1] == "--self-test-control" {
            let data = FileHandle.standardInput.readDataToEndOfFile()
            guard let ids = controlIDs(data), let json = try? JSONSerialization.data(withJSONObject: ids.sorted()) else { fail() }
            FileHandle.standardOutput.write(json); return
        }
        if args.count == 3 && args[1] == "--self-test-receipt" {
            let value = exclusionIsBound(applied: args[2] == "missing" ? nil : 20,
                source: args[2] == "old" ? 19 : 21, matches: args[2] != "unmatched")
            FileHandle.standardOutput.write(Data((value ? "true" : "false").utf8)); return
        }
        let idleTest = args.count == 3 && args[1] == "--self-test-idle" && ["same", "geometry", "identity", "missing"].contains(args[2])
        let rawTest = args.count == 2 && args[1] == "--self-test-raw"
        let occlusionTest = args.count == 3 && args[1] == "--self-test-occlusion" && ["opaque", "transparent", "unexcluded"].contains(args[2])
        if (args.count == 2 && args[1] == "--self-test") || idleTest || rawTest || occlusionTest {
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
            if occlusionTest {
                let privateRect = CGRect(x: 5, y: 5, width: 105, height: 80)
                masks = privateMasks(privateRect,
                    foreground: args[2] == "transparent" ? [] : [CGRect(x: 20, y: 20, width: 70, height: 50)],
                    excluded: args[2] != "unexcluded").map {
                        $0.intersection(CGRect(x: 0, y: 0, width: 128, height: 96))
                    }.filter { !$0.isEmpty }
            }
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
            let capture = Capture(policy: policy, display: requested, filter: filter, excludedAppPIDs: Set(excluded.map { Int($0.processID) }), raw: raw)
            capture.metadataQueue.sync { capture.startMetadata() }
            let stream = SCStream(filter: filter, configuration: config, delegate: capture)
            try stream.addStreamOutput(capture, type: .screen, sampleHandlerQueue: capture.queue)
            try await stream.startCapture()
            capture.queue.async { capture.startHeartbeat(); capture.startControls(stream: stream, selectedDisplay: display) }
            // Retain the stream and callback owner for the entire worker lifetime.
            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                withExtendedLifetime((stream, capture)) {}
            }
        } catch { fail() }
    }
}
