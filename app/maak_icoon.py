# Tekent het app-icoon (Pillow). Uitvoeren vanuit de map app/: python3 maak_icoon.py
from PIL import Image, ImageDraw, ImageFilter
S=2048; k=S/1024
def r(*v): return [x*k for x in v]
img=Image.new("RGBA",(S,S),(0,0,0,0))
# achtergrond: afgeronde tegel (macOS-raster: 824 px met marge 100)
mask=Image.new("L",(S,S),0); ImageDraw.Draw(mask).rounded_rectangle(r(100,100,924,924),radius=185*k,fill=255)
grad=Image.new("RGBA",(S,S))
gd=ImageDraw.Draw(grad)
for y in range(S):
    t=y/S; c=(int(48-(48-17)*t),int(54-(54-19)*t),int(64-(64-24)*t),255); gd.line([(0,y),(S,y)],fill=c)
# schaduw
sh=Image.new("RGBA",(S,S),(0,0,0,0)); ImageDraw.Draw(sh).rounded_rectangle(r(100,118,924,942),radius=185*k,fill=(0,0,0,110))
sh=sh.filter(ImageFilter.GaussianBlur(18*k)); img.alpha_composite(sh)
img.paste(grad,(0,0),mask)
d=ImageDraw.Draw(img)
acc=(242,169,59,255); dark=(26,29,35,255); mid=(70,76,88,255); light=(214,218,224,255); lighter=(236,238,241,255)
# voet
d.rounded_rectangle(r(352,700,672,770),radius=22*k,fill=light)
d.rectangle(r(472,640,552,712),fill=(190,195,203,255))
# juk (U)
d.rounded_rectangle(r(300,330,724,660),radius=60*k,fill=light)
d.rounded_rectangle(r(352,300,672,610),radius=40*k,fill=(0,0,0,0))
# kop
d.ellipse(r(332,232,692,592),fill=lighter)
d.ellipse(r(332,232,692,592),outline=(190,195,203,255),width=int(6*k))
# lens
d.ellipse(r(392,292,632,532),fill=dark)
d.ellipse(r(392,292,632,532),outline=acc,width=int(22*k))
d.ellipse(r(450,350,574,474),fill=(40,46,58,255))
d.ellipse(r(478,378,546,446),fill=(12,14,18,255))
d.ellipse(r(470,362,506,398),fill=(255,255,255,170))
# pan-boog met pijlen
arc=r(232,560,792,860)
d.arc(arc,start=200,end=340,fill=acc,width=int(30*k))
import math
def head(angle,dirsign):
    cx,cy=512*k,710*k; rx,ry=280*k,150*k
    a=math.radians(angle); x=cx+rx*math.cos(a); y=cy+ry*math.sin(a)
    # raaklijn
    tx,ty=-rx*math.sin(a)*dirsign, ry*math.cos(a)*dirsign; n=math.hypot(tx,ty); tx,ty=tx/n,ty/n
    px,py=-ty,tx; L=62*k; W=40*k
    d.polygon([(x+tx*L,y+ty*L),(x+px*W,y+py*W),(x-px*W,y-py*W)],fill=acc)
head(200,-1); head(340,1)
# de boog ligt achter de voet: voet opnieuw tekenen
d.rounded_rectangle(r(352,700,672,770),radius=22*k,fill=light)
img=img.resize((1024,1024),Image.LANCZOS)
img.save("icon.png")
img.save("../PTZ Control.app/Contents/Resources/AppIcon.icns")  # Pillow schrijft alle formaten
img.save("ptz.ico",sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
