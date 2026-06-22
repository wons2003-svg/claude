from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from PIL import Image, ImageDraw, ImageFont
import cairosvg
import base64, io, re

def decode_b64_to_image(b64_data):
    d = re.sub(r'[^A-Za-z0-9+/=]', '', b64_data).rstrip('=')
    if len(d) % 4 == 1:
        d = d[:-1]
    d += '=' * ((4 - len(d) % 4) % 4)
    img_bytes = base64.b64decode(d)
    img = Image.open(io.BytesIO(img_bytes))
    img.load()
    return img

def make_circular_image(img, size=400, top_ratio=0.08, border_width=10):
    img = img.convert("RGBA")
    w, h = img.size
    crop_size = min(w, h)
    left = (w - crop_size) // 2
    top = int(h * top_ratio)
    if top + crop_size > h:
        top = h - crop_size
    img = img.crop((left, top, left + crop_size, top + crop_size))
    inner = size - border_width * 2
    img = img.resize((inner, inner), Image.LANCZOS)
    inner_mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(inner_mask).ellipse((0, 0, inner, inner), fill=255)
    photo_circle = Image.new("RGBA", (inner, inner), (255, 255, 255, 0))
    photo_circle.paste(img, (0, 0), inner_mask)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(output)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(224, 48, 48, 255))
    draw.ellipse((border_width, border_width, size - 1 - border_width, size - 1 - border_width), fill=(255, 255, 255, 255))
    output.paste(photo_circle, (border_width, border_width), photo_circle)
    return output

def make_placeholder_circle(name, size=400):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, size-7, size-7), fill=(230, 220, 205, 255), outline=(224, 48, 48, 255), width=10)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", size // 6)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", size // 6)
        except:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), name, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 5), name, fill=(80, 55, 30, 255), font=font)
    return img

def save_png(img, path):
    img.save(path, format="PNG")
    return path

def svg_to_png(svg_str, out_path, scale=4):
    svg_str = re.sub(r'style="[^"]*"', '', svg_str, count=1)
    svg_str = svg_str.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    cairosvg.svg2png(bytestring=svg_str.encode('utf-8'), write_to=out_path, scale=scale)
    return out_path

with open("/home/user/claude/retirement-banner.html", "r") as f:
    html = f.read()

svgs = re.findall(r'(<svg[^>]*>.*?</svg>)', html, re.DOTALL)

names = ["김보영", "전은영", "장정원", "제경순", "최복례"]
photos = []
for i, name in enumerate(names):
    photo_path = f"/home/user/claude/photo_{name}.jpg"
    img = Image.open(photo_path)
    photos.append(make_circular_image(img))
    print(f"Photo {i} ({name}): OK from {photo_path}")

svg_paths = []
for i, svg in enumerate(svgs):
    path = f"/tmp/svg_deco_{i}.png"
    svg_to_png(svg, path)
    svg_paths.append(path)
    print(f"SVG {i}: rendered")

# --- PPT ---
prs = Presentation()
prs.slide_width = Inches(16)
prs.slide_height = Inches(9)
slide = prs.slides.add_slide(prs.slide_layouts[6])
slide.background.fill.solid()
slide.background.fill.fore_color.rgb = RGBColor(0xFE, 0xF6, 0xE4)

SW, SH = 16, 9

# Inner border frame
border = slide.shapes.add_shape(1, Inches(0.1), Inches(0.1), Inches(SW - 0.2), Inches(SH - 0.2))
border.fill.background()
border.line.color.rgb = RGBColor(0xD4, 0xA0, 0x30)
border.line.width = Pt(0.75)
from pptx.oxml.ns import qn

# SVG decorations - top left and top right
deco_w, deco_h = 3.2, 2.8
slide.shapes.add_picture(svg_paths[0], Inches(0), Inches(0), Inches(deco_w), Inches(deco_h))
slide.shapes.add_picture(svg_paths[1], Inches(SW - deco_w), Inches(0), Inches(deco_w), Inches(deco_h))

# Title
tx = slide.shapes.add_textbox(Inches(3), Inches(1.0), Inches(10), Inches(0.9))
tx.text_frame.word_wrap = True
p = tx.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
r = p.add_run(); r.text = "신세계 강남점  ·  상반기 정년퇴임 기념"
r.font.size = Pt(40); r.font.bold = True; r.font.color.rgb = RGBColor(0x7B, 0x40, 0x20)

# Divider line - left
ln1 = slide.shapes.add_shape(1, Inches(4.2), Inches(2.2), Inches(3.3), Pt(1))
ln1.fill.solid(); ln1.fill.fore_color.rgb = RGBColor(0xC9, 0x85, 0x3A); ln1.line.fill.background()
# Flower
tx2 = slide.shapes.add_textbox(Inches(7.55), Inches(2.0), Inches(0.9), Inches(0.45))
p2 = tx2.text_frame.paragraphs[0]; p2.alignment = PP_ALIGN.CENTER
r2 = p2.add_run(); r2.text = "✿"; r2.font.size = Pt(20); r2.font.color.rgb = RGBColor(0xC9, 0x85, 0x3A)
# Divider line - right
ln2 = slide.shapes.add_shape(1, Inches(8.5), Inches(2.2), Inches(3.3), Pt(1))
ln2.fill.solid(); ln2.fill.fore_color.rgb = RGBColor(0xC9, 0x85, 0x3A); ln2.line.fill.background()

# Main message
tx3 = slide.shapes.add_textbox(Inches(3), Inches(2.7), Inches(10), Inches(1.4))
tx3.text_frame.word_wrap = True
p3 = tx3.text_frame.paragraphs[0]; p3.alignment = PP_ALIGN.CENTER; p3.space_after = Pt(14)
r3 = p3.add_run(); r3.text = "그동안 함께해 주셔서 정말 고마웠습니다"
r3.font.size = Pt(26); r3.font.color.rgb = RGBColor(0x6B, 0x4A, 0x28)
p4 = tx3.text_frame.add_paragraph(); p4.alignment = PP_ALIGN.CENTER
r4 = p4.add_run(); r4.text = "감사한 마음을 담아 특식을 준비했습니다  ·  맛있게 드세요!"
r4.font.size = Pt(26); r4.font.color.rgb = RGBColor(0x6B, 0x4A, 0x28)

# Photo strip background
strip_top = 4.5
sb = slide.shapes.add_shape(1, Inches(0), Inches(strip_top), Inches(SW), Inches(SH - strip_top))
sb.fill.solid(); sb.fill.fore_color.rgb = RGBColor(0xFA, 0xE8, 0xC2); sb.line.fill.background()
ln_top = slide.shapes.add_shape(1, Inches(0), Inches(strip_top), Inches(SW), Pt(1))
ln_top.fill.solid(); ln_top.fill.fore_color.rgb = RGBColor(0xE8, 0xC0, 0x7A); ln_top.line.fill.background()

# People cards
people = [
    {"name": "김보영", "team": "POP운영팀", "years": "23년 1개월"},
    {"name": "전은영", "team": "POP운영팀", "years": "16년 8개월"},
    {"name": "장정원", "team": "POP운영팀", "years": "16년 1개월"},
    {"name": "제경순", "team": "POP운영팀", "years": "15년 8개월"},
    {"name": "최복례", "team": "식품팀", "years": "13년 1개월"},
]

photo_size = 1.8
total_width = 5 * photo_size + 4 * 1.0
start_x = (SW - total_width) / 2

for i, person in enumerate(people):
    cx = start_x + i * (photo_size + 1.0) + photo_size / 2

    # Team badge
    tb = slide.shapes.add_textbox(Inches(cx - 0.6), Inches(strip_top + 0.25), Inches(1.2), Inches(0.35))
    pp = tb.text_frame.paragraphs[0]; pp.alignment = PP_ALIGN.CENTER
    rr = pp.add_run(); rr.text = person["team"]
    rr.font.size = Pt(11); rr.font.bold = True; rr.font.color.rgb = RGBColor(0xB8, 0x70, 0x20)

    # Photo
    img_path = f"/tmp/person_final_{i}.png"
    save_png(photos[i], img_path)
    slide.shapes.add_picture(img_path, Inches(cx - photo_size/2), Inches(strip_top + 0.7), Inches(photo_size), Inches(photo_size))

    # Name
    for text, dy, sz, bold, color in [
        (person["name"], strip_top + 0.7 + photo_size + 0.15, 20, True, (0x2C, 0x15, 0x08)),
        ("파트너님", strip_top + 0.7 + photo_size + 0.5, 14, False, (0x9B, 0x73, 0x40)),
        (person["years"], strip_top + 0.7 + photo_size + 0.8, 13, True, (0xC9, 0x85, 0x3A)),
    ]:
        tb = slide.shapes.add_textbox(Inches(cx - 0.7), Inches(dy), Inches(1.4), Inches(0.3))
        pp = tb.text_frame.paragraphs[0]; pp.alignment = PP_ALIGN.CENTER
        rr = pp.add_run(); rr.text = text
        rr.font.size = Pt(sz); rr.font.bold = bold; rr.font.color.rgb = RGBColor(*color)

prs.save("/home/user/claude/retirement-banner.pptx")
print("Done!")
