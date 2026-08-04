import AppKit
import Foundation

guard CommandLine.arguments.count >= 3 else {
    fputs("Usage: swift scripts/render_grarec.swift summary.json output.png\n", stderr)
    exit(2)
}

let summaryURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let data = try Data(contentsOf: summaryURL)
guard let summary = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
    fatalError("Summary JSON must be an object")
}

struct Palette {
    let background = NSColor(calibratedRed: 0.965, green: 0.979, blue: 0.981, alpha: 1)
    let paper = NSColor.white
    let ink = NSColor(calibratedRed: 0.075, green: 0.130, blue: 0.150, alpha: 1)
    let muted = NSColor(calibratedRed: 0.335, green: 0.405, blue: 0.430, alpha: 1)
    let teal = NSColor(calibratedRed: 0.075, green: 0.475, blue: 0.455, alpha: 1)
    let green = NSColor(calibratedRed: 0.340, green: 0.625, blue: 0.390, alpha: 1)
    let blue = NSColor(calibratedRed: 0.220, green: 0.455, blue: 0.675, alpha: 1)
    let orange = NSColor(calibratedRed: 0.850, green: 0.500, blue: 0.260, alpha: 1)
    let border = NSColor(calibratedRed: 0.815, green: 0.865, blue: 0.875, alpha: 1)
    let dark = NSColor(calibratedRed: 0.055, green: 0.205, blue: 0.225, alpha: 1)
}

let palette = Palette()
let canvas = NSSize(width: 1600, height: 900)

func value(_ key: String, fallback: String = "") -> String {
    guard let raw = summary[key], !(raw is NSNull) else { return fallback }
    if let text = raw as? String { return text }
    if let values = raw as? [Any] { return values.map { readable($0) }.joined(separator: "、") }
    if let dictionary = raw as? [String: Any] {
        let preferred = ["p", "i_or_exposure", "c", "o"]
        let labels = ["p": "P", "i_or_exposure": "I/E", "c": "C", "o": "O"]
        let ordered = preferred.filter { dictionary[$0] != nil } + dictionary.keys.filter { !preferred.contains($0) }.sorted()
        return ordered.map { "\(labels[$0] ?? $0): \(readable(dictionary[$0]!))" }.joined(separator: "\n")
    }
    return String(describing: raw)
}

func readable(_ raw: Any) -> String {
    if let text = raw as? String { return text }
    if let values = raw as? [Any] { return values.map { readable($0) }.joined(separator: "、") }
    if let dictionary = raw as? [String: Any] {
        return dictionary.keys.sorted().map { "\($0): \(readable(dictionary[$0]!))" }.joined(separator: " / ")
    }
    return String(describing: raw)
}

func mapped(_ rect: NSRect) -> NSRect {
    NSRect(x: rect.minX, y: canvas.height - rect.minY - rect.height, width: rect.width, height: rect.height)
}

func font(_ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
    let candidates = ["Hiragino Sans", "Yu Gothic", "Noto Sans JP", "Helvetica Neue"]
    for name in candidates {
        if let base = NSFont(name: name, size: size) {
            if weight == .bold || weight == .heavy || weight == .semibold {
                return NSFontManager.shared.convert(base, toHaveTrait: .boldFontMask)
            }
            return base
        }
    }
    return NSFont.systemFont(ofSize: size, weight: weight)
}

func attributes(size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor = palette.ink, lineHeight: CGFloat? = nil, alignment: NSTextAlignment = .left) -> [NSAttributedString.Key: Any] {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = alignment
    paragraph.lineBreakMode = .byWordWrapping
    if let height = lineHeight {
        paragraph.minimumLineHeight = height
        paragraph.maximumLineHeight = height
    }
    return [.font: font(size, weight), .foregroundColor: color, .paragraphStyle: paragraph]
}

func drawText(_ text: String, rect: NSRect, size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor = palette.ink, lineHeight: CGFloat? = nil, alignment: NSTextAlignment = .left) {
    NSAttributedString(string: text, attributes: attributes(size: size, weight: weight, color: color, lineHeight: lineHeight, alignment: alignment))
        .draw(with: mapped(rect), options: [.usesLineFragmentOrigin, .usesFontLeading])
}

func fittedSize(_ text: String, rect: NSRect, maximum: CGFloat, minimum: CGFloat, weight: NSFont.Weight = .regular) -> CGFloat {
    var candidate = maximum
    while candidate > minimum {
        let lineHeight = candidate * 1.42
        let attributed = NSAttributedString(
            string: text,
            attributes: attributes(size: candidate, weight: weight, lineHeight: lineHeight)
        )
        let bounds = attributed.boundingRect(
            with: NSSize(width: rect.width, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading]
        )
        if bounds.height + candidate * 0.55 <= rect.height { return candidate }
        candidate -= 1
    }
    return minimum
}

func drawFittedText(_ text: String, rect: NSRect, maximum: CGFloat, minimum: CGFloat, weight: NSFont.Weight = .regular, color: NSColor = palette.ink, alignment: NSTextAlignment = .left) {
    let normalized = text.replacingOccurrences(of: "\r", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
    let size = fittedSize(normalized, rect: rect, maximum: maximum, minimum: minimum, weight: weight)
    drawText(normalized, rect: rect, size: size, weight: weight, color: color, lineHeight: size * 1.42, alignment: alignment)
}

func rounded(_ rect: NSRect, radius: CGFloat, fill: NSColor, stroke: NSColor? = nil, width: CGFloat = 1) {
    let path = NSBezierPath(roundedRect: mapped(rect), xRadius: radius, yRadius: radius)
    fill.setFill()
    path.fill()
    if let stroke = stroke {
        stroke.setStroke()
        path.lineWidth = width
        path.stroke()
    }
}

func line(_ x1: CGFloat, _ y1: CGFloat, _ x2: CGFloat, _ y2: CGFloat, color: NSColor, width: CGFloat) {
    let path = NSBezierPath()
    path.move(to: NSPoint(x: x1, y: canvas.height - y1))
    path.line(to: NSPoint(x: x2, y: canvas.height - y2))
    color.setStroke()
    path.lineWidth = width
    path.stroke()
}

func completeSentences(_ text: String, limit: Int) -> String {
    let normalized = text
        .replacingOccurrences(of: "\r", with: "")
        .replacingOccurrences(of: "\n", with: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if normalized.count <= limit { return normalized }
    let pieces = normalized.split(separator: "。", omittingEmptySubsequences: true).map {
        String($0).trimmingCharacters(in: .whitespacesAndNewlines) + "。"
    }
    guard !pieces.isEmpty else { return normalized }
    var selected: [String] = []
    var count = 0
    for sentence in pieces {
        if !selected.isEmpty && count + sentence.count > limit { break }
        selected.append(sentence)
        count += sentence.count
        if count >= limit { break }
    }
    return selected.joined()
}

func card(rect: NSRect, color: NSColor, number: String, title: String, body: String) {
    rounded(rect, radius: 12, fill: palette.paper, stroke: palette.border, width: 2)
    rounded(NSRect(x: rect.minX, y: rect.minY, width: rect.width, height: 9), radius: 5, fill: color)
    rounded(NSRect(x: rect.minX + 20, y: rect.minY + 24, width: 38, height: 38), radius: 19, fill: color.withAlphaComponent(0.14))
    drawText(number, rect: NSRect(x: rect.minX + 20, y: rect.minY + 29, width: 38, height: 27), size: 20, weight: .heavy, color: color, alignment: .center)
    drawText(title, rect: NSRect(x: rect.minX + 70, y: rect.minY + 24, width: rect.width - 92, height: 36), size: 25, weight: .heavy)
    drawFittedText(body, rect: NSRect(x: rect.minX + 22, y: rect.minY + 70, width: rect.width - 44, height: rect.height - 88), maximum: 24, minimum: 15)
}

let image = NSImage(size: canvas)
image.lockFocus()
palette.background.setFill()
NSRect(origin: .zero, size: canvas).fill()

let source = value("source_level", fallback: "source unknown")
let sourceLabel: String
switch source {
case "user_pdf": sourceLabel = "FULL TEXT・提供PDF"
case "pmc_full_text": sourceLabel = "FULL TEXT・PMC"
case "pubmed_abstract": sourceLabel = "ABSTRACT ONLY"
default: sourceLabel = source.uppercased()
}

drawText(sourceLabel, rect: NSRect(x: 54, y: 35, width: 500, height: 34), size: 23, weight: .heavy, color: source == "pubmed_abstract" ? palette.orange : palette.teal)
let headline = value("one_line_summary", fallback: value("take_home_message", fallback: value("title")))
drawFittedText(headline, rect: NSRect(x: 54, y: 78, width: 1065, height: 130), maximum: 48, minimum: 18, weight: .heavy)
line(54, 229, 1546, 229, color: palette.teal, width: 5)

rounded(NSRect(x: 1170, y: 42, width: 376, height: 165), radius: 10, fill: palette.paper, stroke: palette.border, width: 2)
let metadata = "PMID  \(value("pmid"))\nJournal  \(value("journal"))\nYear  \(value("year"))\nType  \(value("study_type"))"
drawFittedText(metadata, rect: NSRect(x: 1193, y: 60, width: 330, height: 132), maximum: 20, minimum: 14, weight: .semibold)

card(rect: NSRect(x: 54, y: 260, width: 730, height: 255), color: palette.teal, number: "1", title: "PICO / 対象", body: completeSentences(value("pico", fallback: "原文確認"), limit: 230))
card(rect: NSRect(x: 808, y: 260, width: 738, height: 255), color: palette.blue, number: "2", title: "主要結果", body: completeSentences(value("main_results", fallback: "数値は原文確認"), limit: 230))
card(rect: NSRect(x: 54, y: 535, width: 730, height: 255), color: palette.green, number: "3", title: "診療への意味", body: completeSentences(value("applicability_to_japanese_pediatric_clinic", fallback: value("clinical_impact", fallback: "原文確認")), limit: 210))
card(rect: NSRect(x: 808, y: 535, width: 738, height: 255), color: palette.orange, number: "4", title: "限界・注意", body: completeSentences(value("limitations", fallback: value("safety", fallback: "原文確認")), limit: 210))

line(54, 821, 54, 860, color: palette.orange, width: 5)
let footer = source == "pubmed_abstract"
    ? "AI下読み用・Abstractのみ。主要数値、対象、評価項目、結論を全文で確認してください。"
    : "AI下読み用。主要数値、対象、評価項目、結論を原文で確認し、Human Checkedを更新してください。"
drawText(footer, rect: NSRect(x: 72, y: 817, width: 940, height: 48), size: 19, weight: .semibold, color: palette.muted)
drawFittedText(value("title"), rect: NSRect(x: 1030, y: 819, width: 516, height: 44), maximum: 17, minimum: 10, color: palette.muted, alignment: .right)

image.unlockFocus()
guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Failed to render PNG")
}
try FileManager.default.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)
try png.write(to: outputURL)
print(outputURL.path)
