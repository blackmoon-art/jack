from PIL import Image, ImageDraw, ImageFont
import os

os.makedirs('charts', exist_ok=True)

# Create image
img = Image.new('RGB', (1024, 1024), (30, 30, 40))
draw = ImageDraw.Draw(img)

# Background gradient
for y in range(1024):
    r = int(35 + (y/1024)*15)
    g = int(35 + (y/1024)*10)
    b = int(45 + (y/1024)*5)
    draw.line([(0, y), (1024, y)], fill=(r, g, b))

# City skyline silhouette
skyline_color = (50, 55, 70)
for x_offset, w, h in [(50, 80, 180), (150, 60, 250), (240, 100, 150), (370, 70, 280),
                        (470, 90, 200), (580, 60, 300), (670, 110, 170), (800, 70, 260),
                        (890, 90, 190), (100, 60, 220), (720, 80, 200)]:
    draw.rectangle([x_offset, 1024-h, x_offset+w, 1024], fill=skyline_color)

# Warm glow behind figure
for rr in range(600, 100, -10):
    draw.ellipse([512-rr//2, 450-rr//2, 512+rr//2, 450+rr//2],
                 outline=(60+rr//30, 55+rr//30, 50), width=1)

# === Face ===
face_cx, face_cy = 512, 430
face_w, face_h = 190, 250

draw.chord([face_cx-face_w//2, face_cy-face_h//2, face_cx+face_w//2, face_cy+face_h//2],
           0, 360, fill=(235, 215, 185))

# Skin gradient
for y_offset in range(face_h):
    y_pos = face_cy - face_h//2 + y_offset
    ratio = y_offset / face_h
    r_skin = int(235 - ratio*15)
    g_skin = int(215 - ratio*10)
    b_skin = int(185 - ratio*10)
    x_radius = int(face_w//2 * (1 - ((y_offset - face_h//2) / (face_h//2))**2 * 0.15))
    draw.chord([face_cx-x_radius, y_pos, face_cx+x_radius, y_pos+1], 0, 360, fill=(r_skin, g_skin, b_skin))

# Hair
hair_color = (20, 18, 15)
draw.ellipse([face_cx-120, face_cy-190, face_cx+120, face_cy-50], fill=hair_color)
draw.rectangle([face_cx-130, face_cy-140, face_cx-85, face_cy-20], fill=hair_color)
draw.rectangle([face_cx+85, face_cy-140, face_cx+130, face_cy-20], fill=hair_color)
draw.ellipse([face_cx-115, face_cy-195, face_cx+115, face_cy-60], fill=(25, 22, 18))

# Big ears
ear_color = (230, 208, 178)
draw.ellipse([face_cx-face_w//2-35, face_cy-30, face_cx-face_w//2+15, face_cy+60], fill=ear_color)
draw.ellipse([face_cx-face_w//2-30, face_cy-25, face_cx-face_w//2+10, face_cy+55], fill=(220, 198, 168))
draw.ellipse([face_cx+face_w//2-15, face_cy-30, face_cx+face_w//2+35, face_cy+60], fill=ear_color)
draw.ellipse([face_cx+face_w//2-10, face_cy-25, face_cx+face_w//2+30, face_cy+55], fill=(220, 198, 168))

# Eyebrows
brow_color = (30, 28, 25)
draw.arc([face_cx-85, face_cy-100, face_cx-15, face_cy-60], 220, 320, fill=brow_color, width=5)
draw.arc([face_cx+15, face_cy-100, face_cx+85, face_cy-60], 220, 320, fill=brow_color, width=5)

# Eyes
eye_color = (40, 38, 35)
white_color = (240, 240, 235)
draw.ellipse([face_cx-75, face_cy-55, face_cx-30, face_cy-30], fill=white_color)
draw.ellipse([face_cx-72, face_cy-52, face_cx-33, face_cy-33], fill=(220, 218, 210))
draw.ellipse([face_cx-60, face_cy-48, face_cx-42, face_cy-37], fill=eye_color)
draw.ellipse([face_cx-56, face_cy-46, face_cx-46, face_cy-39], fill=(50, 48, 45))
draw.ellipse([face_cx-54, face_cy-44, face_cx-50, face_cy-41], fill=(255, 255, 250))

draw.ellipse([face_cx+30, face_cy-55, face_cx+75, face_cy-30], fill=white_color)
draw.ellipse([face_cx+33, face_cy-52, face_cx+72, face_cy-33], fill=(220, 218, 210))
draw.ellipse([face_cx+42, face_cy-48, face_cx+60, face_cy-37], fill=eye_color)
draw.ellipse([face_cx+46, face_cy-46, face_cx+56, face_cy-39], fill=(50, 48, 45))
draw.ellipse([face_cx+50, face_cy-44, face_cx+54, face_cy-41], fill=(255, 255, 250))

draw.arc([face_cx-80, face_cy-58, face_cx-25, face_cy-28], 200, 340, fill=(60, 58, 55), width=2)
draw.arc([face_cx+25, face_cy-58, face_cx+80, face_cy-28], 200, 340, fill=(60, 58, 55), width=2)

# Nose
nose_color = (210, 190, 165)
draw.line([(face_cx, face_cy-20), (face_cx, face_cy+15)], fill=nose_color, width=4)
draw.arc([face_cx-15, face_cy-25, face_cx+15, face_cy+20], 270, 90, fill=nose_color, width=3)
draw.ellipse([face_cx-12, face_cy+10, face_cx-3, face_cy+18], fill=(170, 150, 130))
draw.ellipse([face_cx+3, face_cy+10, face_cx+12, face_cy+18], fill=(170, 150, 130))

# Mouth
draw.arc([face_cx-30, face_cy+40, face_cx+30, face_cy+55], 200, 340, fill=(180, 80, 70), width=4)
draw.arc([face_cx-25, face_cy+45, face_cx+25, face_cy+65], 10, 170, fill=(190, 90, 80), width=3)
draw.line([(face_cx-28, face_cy+48), (face_cx+28, face_cy+48)], fill=(160, 65, 55), width=2)

# Neck
neck_color = (215, 195, 165)
draw.rectangle([face_cx-50, face_cy+110, face_cx+50, face_cy+170], fill=neck_color)
draw.ellipse([face_cx-55, face_cy+95, face_cx+55, face_cy+125], fill=(200, 180, 155))

# Suit
suit_color = (25, 35, 55)
draw.polygon([(face_cx-220, face_cy+140), (face_cx-180, face_cy+115),
              (face_cx-120, face_cy+110), (face_cx-60, face_cy+115),
              (face_cx-50, face_cy+140), (face_cx-50, face_cy+350),
              (face_cx-220, face_cy+350)], fill=suit_color)
draw.polygon([(face_cx+220, face_cy+140), (face_cx+180, face_cy+115),
              (face_cx+120, face_cy+110), (face_cx+60, face_cy+115),
              (face_cx+50, face_cy+140), (face_cx+50, face_cy+350),
              (face_cx+220, face_cy+350)], fill=suit_color)
draw.rectangle([face_cx-140, face_cy+120, face_cx+140, face_cy+380], fill=suit_color)

# Shirt and tie
shirt_color = (240, 238, 232)
draw.polygon([(face_cx-35, face_cy+110), (face_cx, face_cy+145), (face_cx-25, face_cy+155)], fill=shirt_color)
draw.polygon([(face_cx+35, face_cy+110), (face_cx, face_cy+145), (face_cx+25, face_cy+155)], fill=shirt_color)

tie_color = (140, 30, 30)
draw.polygon([(face_cx-12, face_cy+140), (face_cx+12, face_cy+140),
              (face_cx+8, face_cy+230), (face_cx-8, face_cy+230)], fill=tie_color)
draw.rectangle([face_cx-14, face_cy+135, face_cx+14, face_cy+150], fill=tie_color)
draw.rectangle([face_cx-16, face_cy+132, face_cx+16, face_cy+142], fill=(160, 40, 40))

# Lapels
lapel_color = (30, 42, 62)
draw.polygon([(face_cx-100, face_cy+115), (face_cx-35, face_cy+110),
              (face_cx-20, face_cy+155), (face_cx-60, face_cy+145)], fill=lapel_color)
draw.polygon([(face_cx+100, face_cy+115), (face_cx+35, face_cy+110),
              (face_cx+20, face_cy+155), (face_cx+60, face_cy+145)], fill=lapel_color)

# Pocket square
draw.line([(face_cx+45, face_cy+145), (face_cx+95, face_cy+145)], fill=(40, 52, 72), width=2)
draw.polygon([(face_cx+60, face_cy+145), (face_cx+80, face_cy+145),
              (face_cx+75, face_cy+130), (face_cx+65, face_cy+130)], fill=(200, 190, 180))

# Long arms
draw.polygon([(face_cx-180, face_cy+140), (face_cx-200, face_cy+160),
              (face_cx-250, face_cy+280), (face_cx-220, face_cy+300),
              (face_cx-170, face_cy+250)], fill=suit_color)
hand_color = (225, 205, 175)
draw.ellipse([face_cx-260, face_cy+270, face_cx-220, face_cy+300], fill=hand_color)

draw.polygon([(face_cx+180, face_cy+140), (face_cx+200, face_cy+160),
              (face_cx+250, face_cy+280), (face_cx+220, face_cy+300),
              (face_cx+170, face_cy+250)], fill=suit_color)
draw.ellipse([face_cx+220, face_cy+270, face_cx+260, face_cy+300], fill=hand_color)

# Text
font_large = ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc', 48)
font_small = ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc', 26)

draw.rectangle([100, 870, 924, 975], fill=(20, 25, 35))
draw.rectangle([100, 870, 924, 975], outline=(100, 120, 150), width=2)

draw.text((512, 905), '刘 备  .  现代篇', fill=(220, 200, 170), font=font_large, anchor='mm')
draw.text((512, 952), '身长七尺五寸  .  双手过膝  .  面如冠玉  .  无须', fill=(160, 170, 190), font=font_small, anchor='mm')

draw.rectangle([0, 990, 1024, 1024], fill=(15, 18, 28))
draw.text((512, 1007), '三国志.先主传 | 身长七尺五寸，垂手过膝，顾自见其耳，无须',
          fill=(120, 130, 150), font=font_small, anchor='mm')

img.save('charts/liu_bei_modern.png')
print('Image saved successfully as charts/liu_bei_modern.png')
