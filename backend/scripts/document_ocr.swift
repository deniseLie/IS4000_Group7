// Credential-free local OCR. Emits tesseract-compatible TSV, never writes document text to logs.
import Foundation
import Vision
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = (ProcessInfo.processInfo.environment["DOCUMENT_OCR_VISION_LANGUAGES"] ?? "en-US").split(separator: ",").map(String.init)
request.usesLanguageCorrection = false
do {
    try VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1])).perform([request])
    print("level\tpage_num\tblock_num\tpar_num\tline_num\tconf\ttext")
    for (index, observation) in (request.results ?? []).enumerated() {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let text = candidate.string.replacingOccurrences(of: "\t", with: " ").replacingOccurrences(of: "\n", with: " ")
        print("5\t1\t1\t1\t\(index + 1)\t\(candidate.confidence * 100)\t\(text)")
    }
} catch { exit(1) }
