import Foundation
import Vision
import ImageIO

// Input/output are pipes only. Never save images or print recognition diagnostics.
struct Row: Codable {
    let text: String
    let x: Int
    let y: Int
    let w: Int
    let h: Int
}

func recognize() throws -> Data {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard !input.isEmpty,
          let source = CGImageSourceCreateWithData(input as CFData, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "LocalOCR", code: 1)
    }
    let request = VNRecognizeTextRequest()
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard arguments.isEmpty || arguments == ["--fast"] else {
        throw NSError(domain: "LocalOCR", code: 2)
    }
    request.recognitionLevel = arguments == ["--fast"] ? .fast : .accurate
    request.minimumTextHeight = 0
    request.usesLanguageCorrection = false
    let supported = try request.supportedRecognitionLanguages()
    let preferred = ["en-US", "fr-FR", "de-DE"].filter { supported.contains($0) }
    if !preferred.isEmpty { request.recognitionLanguages = preferred }
    request.automaticallyDetectsLanguage = true
    let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
    try handler.perform([request])
    let width = Double(image.width), height = Double(image.height)
    let rows: [Row] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first,
              !candidate.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        let box = observation.boundingBox
        let x1 = max(0, min(image.width, Int(floor(box.minX * width))))
        let y1 = max(0, min(image.height, Int(floor((1 - box.maxY) * height))))
        let x2 = max(0, min(image.width, Int(ceil(box.maxX * width))))
        let y2 = max(0, min(image.height, Int(ceil((1 - box.minY) * height))))
        guard x2 > x1, y2 > y1 else { return nil }
        return Row(text: candidate.string, x: x1, y: y1, w: x2 - x1, h: y2 - y1)
    }
    return try JSONEncoder().encode(rows)
}

do {
    FileHandle.standardOutput.write(try recognize())
} catch {
    FileHandle.standardError.write(Data("Local text recognition failed\n".utf8))
    exit(1)
}
