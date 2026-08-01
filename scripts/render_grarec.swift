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
        .draw(with: mapped(rect), options: [.usesLineFragmentOrigin, .usesFontLeading, .truncatesLastVisibleLine])
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

func compact(_ text: String, limit: Int) -> String {
    let normalized = text.replacingOccurrences(of: "\r", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
    if normalized.count <= limit { return normalized }
    return String(normalized.prefix(limit - 1)) + "…"
}

func card(x: CGFloat, color: NSColor, number: String, title: String, body: String) {
    let rect = NSRect(x: x, y: 270, width: 358, height: 360)
    rounded(rect, radius: 12, fill: palette.paper, stroke: palette.border, width: 2)
    rounded(NSRect(x: x, y: 270, width: 358, height: 11), radius: 6, fill: color)
    rounded(NSRect(x: x + 22, y: 303, width: 46, height: 46), radius: 23, fill: color.withAlphaComponent(0.14))
    drawText(number, rect: NSRect(x: x + 22, y: 309, width: 46, height: 32), size: 24, weight: .heavy, color: color, alignment: .center)
    drawText(title, rect: NSRect(x: x + 82, y: 303, width: 250, height: 42), size: 29, weight: .heavy)
    drawText(compact(body, limit: 260), rect: NSRect(x: x + 25, y: 370, width: 308, height: 225), size: 21, color: palette.ink, lineHeight: 31)
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
let headline = compact(value("one_line_summary", fallback: value("take_home_message", fallback: value("title"))), limit: 84)
drawText(headline, rect: NSRect(x: 54, y: 78, width: 1065, height: 130), size: 48, weight: .heavy, lineHeight: 56)
line(54, 229, 1546, 229, color: palette.teal, width: 5)

rounded(NSRect(x: 1170, y: 42, width: 376, height: 165), radius: 10, fill: palette.paper, stroke: palette.border, width: 2)
let metadata = "PMID  \(value("pmid"))\nJournal  \(compact(value("journal"), limit: 38))\nYear  \(value("year"))\nType  \(compact(value("study_type"), limit: 35))"
drawText(metadata, rect: NSRect(x: 1193, y: 60, width: 330, height: 132), size: 20, weight: .semibold, lineHeight: 30)

card(x: 54, color: palette.teal, number: "1", title: "PICO / 対象", body: value("pico", fallback: "原文確認"))
card(x: 432, color: palette.blue, number: "2", title: "主要結果", body: value("main_results", fallback: "数値は原文確認"))
card(x: 810, color: palette.green, number: "3", title: "診療への意味", body: value("applicability_to_japanese_pediatric_clinic", fallback: value("clinical_impact", fallback: "原文確認")))
card(x: 1188, color: palette.orange, number: "4", title: "限界・注意", body: value("limitations", fallback: value("safety", fallback: "原文確認")))

rounded(NSRect(x: 54, y: 660, width: 1492, height: 130), radius: 12, fill: palette.dark)
drawText("TAKE HOME", rect: NSRect(x: 82, y: 700, width: 190, height: 38), size: 25, weight: .heavy, color: NSColor(calibratedRed: 0.72, green: 0.91, blue: 0.84, alpha: 1))
drawText(compact(value("one_line_summary", fallback: "原文確認が必要です"), limit: 120), rect: NSRect(x: 286, y: 683, width: 1215, height: 88), size: 28, weight: .heavy, color: .white, lineHeight: 37)

line(54, 821, 54, 860, color: palette.orange, width: 5)
let footer = source == "pubmed_abstract"
    ? "AI下読み用・Abstractのみ。主要数値、対象、評価項目、結論を全文で確認してください。"
    : "AI下読み用。主要数値、対象、評価項目、結論を原文で確認し、Human Checkedを更新してください。"
drawText(footer, rect: NSRect(x: 72, y: 817, width: 940, height: 48), size: 19, weight: .semibold, color: palette.muted)
drawText(compact(value("title"), limit: 75), rect: NSRect(x: 1030, y: 819, width: 516, height: 44), size: 17, color: palette.muted, alignment: .right)

image.unlockFocus()
guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Failed to render PNG")
}
try FileManager.default.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)
try png.write(to: outputURL)
print(outputURL.path)
