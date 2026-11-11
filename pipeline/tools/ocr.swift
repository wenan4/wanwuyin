// Local OCR via Apple Vision: prints JSON [{text, conf, box:[x0,y0,x1,y1]}] in image pixels (top-left origin).
// Build: swiftc -O tools/ocr.swift -o tools/ocr     Run: tools/ocr image.png
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1,
      let img = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: ocr <image>\n".data(using: .utf8)!)
    exit(1)
}
let W = Double(cg.width), H = Double(cg.height)
let req = VNRecognizeTextRequest()
req.recognitionLanguages = ["zh-Hans", "en-US"]
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false  // labels are single characters; correction hurts
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
var out: [[String: Any]] = []
for obs in req.results ?? [] {
    guard let best = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox  // normalised, bottom-left origin
    out.append(["text": best.string, "conf": best.confidence,
                "box": [b.minX * W, (1 - b.maxY) * H, b.maxX * W, (1 - b.minY) * H]])
}
let data = try JSONSerialization.data(withJSONObject: out, options: [])
print(String(data: data, encoding: .utf8)!)
