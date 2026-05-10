import AppKit
import Foundation

struct Style {
    let bg = NSColor(calibratedRed: 0.984, green: 0.992, blue: 0.992, alpha: 1)
    let ink = NSColor(calibratedRed: 0.098, green: 0.137, blue: 0.149, alpha: 1)
    let muted = NSColor(calibratedRed: 0.353, green: 0.416, blue: 0.427, alpha: 1)
    let teal = NSColor(calibratedRed: 0.180, green: 0.490, blue: 0.478, alpha: 1)
    let green = NSColor(calibratedRed: 0.482, green: 0.663, blue: 0.353, alpha: 1)
    let orange = NSColor(calibratedRed: 0.824, green: 0.545, blue: 0.298, alpha: 1)
    let blue = NSColor(calibratedRed: 0.298, green: 0.498, blue: 0.639, alpha: 1)
    let border = NSColor(calibratedRed: 0.835, green: 0.882, blue: 0.886, alpha: 1)
    let white = NSColor.white
}

let style = Style()
let size = NSSize(width: 1600, height: 900)
let output = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "images/2026/05/PMID_41733080_grarec.png"

func mapped(_ rect: NSRect) -> NSRect {
    NSRect(x: rect.minX, y: size.height - rect.minY - rect.height, width: rect.width, height: rect.height)
}

func mapped(_ point: NSPoint) -> NSPoint {
    NSPoint(x: point.x, y: size.height - point.y)
}

func font(_ size: CGFloat, weight: NSFont.Weight = .regular) -> NSFont {
    let candidates = ["Hiragino Sans", "Yu Gothic", "Noto Sans JP", "Helvetica Neue"]
    for name in candidates {
        if let f = NSFont(name: name, size: size) {
            let manager = NSFontManager.shared
            return manager.convert(f, toHaveTrait: weight == .bold || weight == .heavy ? .boldFontMask : [])
        }
    }
    return NSFont.systemFont(ofSize: size, weight: weight)
}

func paragraph(_ alignment: NSTextAlignment = .left, lineHeight: CGFloat? = nil) -> NSMutableParagraphStyle {
    let p = NSMutableParagraphStyle()
    p.alignment = alignment
    if let lineHeight {
        p.minimumLineHeight = lineHeight
        p.maximumLineHeight = lineHeight
    }
    return p
}

func attrs(size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor = style.ink, lineHeight: CGFloat? = nil) -> [NSAttributedString.Key: Any] {
    return [
        .font: font(size, weight: weight),
        .foregroundColor: color,
        .paragraphStyle: paragraph(.left, lineHeight: lineHeight)
    ]
}

func drawText(_ text: String, in rect: NSRect, size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor = style.ink, lineHeight: CGFloat? = nil) {
    NSString(string: text).draw(in: mapped(rect), withAttributes: attrs(size: size, weight: weight, color: color, lineHeight: lineHeight))
}

func drawRounded(_ rect: NSRect, radius: CGFloat, fill: NSColor, stroke: NSColor? = nil, width: CGFloat = 1) {
    let path = NSBezierPath(roundedRect: mapped(rect), xRadius: radius, yRadius: radius)
    fill.setFill()
    path.fill()
    if let stroke {
        stroke.setStroke()
        path.lineWidth = width
        path.stroke()
    }
}

func drawLine(from: NSPoint, to: NSPoint, color: NSColor, width: CGFloat) {
    let path = NSBezierPath()
    path.move(to: mapped(from))
    path.line(to: mapped(to))
    color.setStroke()
    path.lineWidth = width
    path.stroke()
}

func drawBullet(_ text: String, x: CGFloat, y: CGFloat, width: CGFloat, size: CGFloat = 21) -> CGFloat {
    drawText("•", in: NSRect(x: x, y: y - 2, width: 24, height: 34), size: size + 3, weight: .bold, color: style.teal)
    let rect = NSRect(x: x + 28, y: y, width: width - 28, height: 110)
    let attributed = NSAttributedString(string: text, attributes: attrs(size: size, weight: .regular, color: style.ink, lineHeight: 31))
    let used = attributed.boundingRect(with: rect.size, options: [.usesLineFragmentOrigin, .usesFontLeading]).height
    attributed.draw(in: mapped(NSRect(x: rect.minX, y: rect.minY, width: rect.width, height: ceil(used) + 6)))
    return y + ceil(used) + 12
}

func drawCircleLabel(_ label: String, center: NSPoint, color: NSColor) {
    color.withAlphaComponent(0.14).setFill()
    NSBezierPath(ovalIn: mapped(NSRect(x: center.x - 23, y: center.y - 23, width: 46, height: 46))).fill()
    let labelRect = NSRect(x: center.x - 23, y: center.y - 17, width: 46, height: 34)
    let p = paragraph(.center)
    NSString(string: label).draw(in: mapped(labelRect), withAttributes: [
        .font: font(26, weight: .heavy),
        .foregroundColor: color,
        .paragraphStyle: p
    ])
}

func drawCard(x: CGFloat, y: CGFloat, w: CGFloat, h: CGFloat, color: NSColor, number: String, title: String, bullets: [String]) {
    let rect = NSRect(x: x, y: y, width: w, height: h)
    drawRounded(rect, radius: 8, fill: style.white, stroke: style.border, width: 2)
    drawRounded(NSRect(x: x, y: y, width: w, height: 10), radius: 5, fill: color)
    drawCircleLabel(number, center: NSPoint(x: x + 47, y: y + 58), color: color)
    drawText(title, in: NSRect(x: x + 82, y: y + 35, width: w - 105, height: 42), size: 31, weight: .heavy)
    var cy = y + 100
    for item in bullets {
        cy = drawBullet(item, x: x + 25, y: cy, width: w - 48)
    }
}

func drawFlowCard(x: CGFloat, y: CGFloat, w: CGFloat, h: CGFloat) {
    let rect = NSRect(x: x, y: y, width: w, height: h)
    drawRounded(rect, radius: 8, fill: style.white, stroke: style.border, width: 2)
    drawRounded(NSRect(x: x, y: y, width: w, height: 10), radius: 5, fill: style.blue)
    drawCircleLabel("3", center: NSPoint(x: x + 47, y: y + 58), color: style.blue)
    drawText("病態仮説", in: NSRect(x: x + 82, y: y + 35, width: w - 105, height: 42), size: 31, weight: .heavy)

    let labels = ["抗ネフリン抗体", "スリット膜障害", "足突起消失", "蛋白尿"]
    var cy: CGFloat = y + 115
    for (idx, label) in labels.enumerated() {
        let pill = NSRect(x: x + 46, y: cy, width: w - 92, height: 48)
        drawRounded(pill, radius: 24, fill: NSColor(calibratedRed: 0.969, green: 0.984, blue: 0.984, alpha: 1), stroke: NSColor(calibratedRed: 0.78, green: 0.85, blue: 0.85, alpha: 1), width: 2)
        let p = paragraph(.center)
        NSString(string: label).draw(in: mapped(NSRect(x: pill.minX, y: pill.minY + 9, width: pill.width, height: 34)), withAttributes: [
            .font: font(24, weight: .heavy),
            .foregroundColor: NSColor(calibratedRed: 0.157, green: 0.361, blue: 0.353, alpha: 1),
            .paragraphStyle: p
        ])
        cy += 61
        if idx < labels.count - 1 {
            drawText("↓", in: NSRect(x: x + w / 2 - 12, y: cy - 9, width: 40, height: 34), size: 29, weight: .heavy, color: style.green)
            cy += 24
        }
    }
}

let image = NSImage(size: size)
image.lockFocus()
NSColor(calibratedRed: 0.957, green: 0.973, blue: 0.976, alpha: 1).setFill()
NSRect(origin: .zero, size: size).fill()
drawRounded(NSRect(x: 0, y: 0, width: size.width, height: size.height), radius: 0, fill: style.bg)

drawText("腎臓・糸球体レビュー", in: NSRect(x: 54, y: 40, width: 560, height: 36), size: 26, weight: .heavy, color: style.teal)
drawText("ポドサイトスリット膜は\n抗ネフリン抗体の標的となる", in: NSRect(x: 54, y: 84, width: 1010, height: 126), size: 52, weight: .heavy, lineHeight: 58)
drawLine(from: NSPoint(x: 54, y: 230), to: NSPoint(x: 1546, y: 230), color: style.teal, width: 5)

let meta = NSRect(x: 1198, y: 45, width: 348, height: 166)
drawRounded(meta, radius: 8, fill: style.white, stroke: NSColor(calibratedRed: 0.745, green: 0.835, blue: 0.831, alpha: 1), width: 2)
drawText("PMID 41733080\nJournal  Current Opinion in Nephrology and Hypertension\nYear 2026\nType Review", in: NSRect(x: 1218, y: 61, width: 308, height: 136), size: 22, weight: .semibold, color: style.ink, lineHeight: 32)

let cardY: CGFloat = 256
let cardH: CGFloat = 390
let gap: CGFloat = 20
let cardW: CGFloat = (1492 - gap * 3) / 4
drawCard(
    x: 54,
    y: cardY,
    w: cardW,
    h: cardH,
    color: style.teal,
    number: "1",
    title: "背景",
    bullets: [
        "ポドサイトのスリット膜構造に新知見",
        "抗ネフリン抗体が後天性ポドサイト疾患で注目",
        "小児ステロイド感受性ネフローゼ、MCD、一部FSGSが関連"
    ]
)
drawCard(
    x: 54 + (cardW + gap),
    y: cardY,
    w: cardW,
    h: cardH,
    color: style.green,
    number: "2",
    title: "新しい理解",
    bullets: [
        "ネフリン、Neph1、関連タンパクが多層構造を形成",
        "高解像度プロテオミクス、cryo-ETなどで解析が進展",
        "単なるフィルターではなく、シグナル伝達の場として整理"
    ]
)
drawFlowCard(x: 54 + (cardW + gap) * 2, y: cardY, w: cardW, h: cardH)
drawCard(
    x: 54 + (cardW + gap) * 3,
    y: cardY,
    w: cardW,
    h: cardH,
    color: style.orange,
    number: "4",
    title: "臨床への意味",
    bullets: [
        "抗ネフリン抗体測定は診断・予後・治療選択に影響しうる",
        "標的エピトープとネフリン内在化の機序は今後の課題",
        "広く使える信頼性の高い測定系が必要"
    ]
)

let take = NSRect(x: 54, y: 674, width: 1492, height: 118)
drawRounded(take, radius: 8, fill: NSColor(calibratedRed: 0.082, green: 0.220, blue: 0.227, alpha: 1))
drawText("Take Home", in: NSRect(x: 84, y: 715, width: 185, height: 34), size: 26, weight: .heavy, color: NSColor(calibratedRed: 0.749, green: 0.890, blue: 0.839, alpha: 1))
drawText("抗ネフリン抗体は、後天性ポドサイト疾患の診断・予後・治療選択に影響しうる注目標的として整理されている。", in: NSRect(x: 288, y: 696, width: 1218, height: 86), size: 27, weight: .heavy, color: .white, lineHeight: 36)

drawLine(from: NSPoint(x: 54, y: 819), to: NSPoint(x: 54, y: 858), color: style.orange, width: 5)
drawText("AI下読み用。診療変更の判断前に、対象疾患、検査法、臨床研究の位置づけを原文で確認。", in: NSRect(x: 72, y: 818, width: 920, height: 42), size: 20, weight: .semibold, color: style.muted)
drawText("Title: The podocyte slit-diaphragm: target of anti-nephrin antibodies.", in: NSRect(x: 1040, y: 820, width: 506, height: 40), size: 19, weight: .regular, color: style.muted)

image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Failed to render PNG")
}
try png.write(to: URL(fileURLWithPath: output))
print(output)
