import Foundation
import ScreenCaptureKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

@main
struct NativeCapture {
    static func fail() -> Never {
        FileHandle.standardError.write(Data("Local display capture failed\n".utf8))
        exit(1)
    }

    static func main() async {
        guard CommandLine.arguments.count == 5 else { fail() }
        let values = CommandLine.arguments.dropFirst().compactMap(Int.init)
        guard values.count == 4, values[2] > 0, values[3] > 0,
              values[2] <= 32768, values[3] <= 32768 else { fail() }
        let requested = CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
        do {
            // Display selection is exact; never substitute another display or all screens.
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let matches = content.displays.filter { CGDisplayBounds($0.displayID) == requested }
            guard matches.count == 1, let display = matches.first else { fail() }
            let filter = SCContentFilter(display: display, excludingWindows: [])
            let configuration = SCStreamConfiguration()
            configuration.width = values[2]
            configuration.height = values[3]
            configuration.showsCursor = true
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
            guard image.width == values[2], image.height == values[3],
                  let encoded = CFDataCreateMutable(nil, 0),
                  let destination = CGImageDestinationCreateWithData(encoded, UTType.png.identifier as CFString, 1, nil) else {
                fail()
            }
            CGImageDestinationAddImage(destination, image, nil)
            guard CGImageDestinationFinalize(destination) else { fail() }
            FileHandle.standardOutput.write(encoded as Data)
        } catch {
            fail()
        }
    }
}
