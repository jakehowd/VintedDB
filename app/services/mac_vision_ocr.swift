import Foundation
import Vision
import ImageIO

struct OCRResult: Codable {
    let path: String
    let lines: [String]
}

func recognizeText(at filePath: String) -> [String] {
    let url = URL(fileURLWithPath: filePath)
    guard let imgSource = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(imgSource, 0, nil) else {
        return []
    }

    var collected: [String] = []
    let request = VNRecognizeTextRequest { req, _ in
        guard let observations = req.results as? [VNRecognizedTextObservation] else { return }
        for obs in observations {
            if let top = obs.topCandidates(1).first, top.confidence >= 0.30 {
                let cleaned = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
                if !cleaned.isEmpty {
                    collected.append(cleaned)
                }
            }
        }
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.012

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? handler.perform([request])
    return collected
}

let args = Array(CommandLine.arguments.dropFirst())
var output: [String: [String]] = [:]
for path in args {
    output[path] = recognizeText(at: path)
}

if let jsonData = try? JSONSerialization.data(withJSONObject: output, options: []),
   let jsonStr = String(data: jsonData, encoding: .utf8) {
    print(jsonStr)
} else {
    print("{}")
}

