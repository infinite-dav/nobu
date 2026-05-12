#!/usr/bin/env python3
"""Generate the invitation email HTML for Mailchimp automation copy-paste.
Usage: python3 generate_automation_email.py szerda|csutortok"""
import sys

SERVER_URL = "https://nobu-rsvp.onrender.com"

IMG_MAY27 = "https://mcusercontent.com/99977b9e1589502e522f30db3/images/e6542144-a17e-b03d-f7de-15b1c39d0702.jpg"
IMG_MAY28 = "https://mcusercontent.com/99977b9e1589502e522f30db3/images/488288ab-dc46-0507-690d-245d176b43a3.jpg"
LOGO = "https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png"

day = sys.argv[1] if len(sys.argv) > 1 else "szerda"

if day == "szerda":
    img = IMG_MAY27
    date_text = "2026. MÁJUS 27"
    day_label = "Szerda"
else:
    img = IMG_MAY28
    date_text = "2026. MÁJUS 28"
    day_label = "Csütörtök"

subject = f"NOBU Budapest Opening Dinner – Meghívó | {date_text}"
preview = "Örömmel meghívjuk Önt és partnerét a megújult Nobu Budapest exkluzív nyitóeseményére."

html = f"""<!DOCTYPE html>
<html lang="hu">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>NOBU Budapest</title></head>
<body style="margin:0;padding:0;background-color:#0a1628;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a1628;">
<tr><td align="center" style="padding:20px 10px;">

<!-- Hero Image -->
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background-color:#0a1628;border:2px solid #c8a960;">
<tr><td align="center" style="padding:0;">
<img src="{img}" alt="NOBU BUDAPEST - Opening Dinner - {date_text}" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;" border="0">
</td></tr>

<!-- RSVP Buttons -->
<tr><td align="center" style="padding:30px;">
<table cellpadding="0" cellspacing="0"><tr>

<!-- YES button -->
<td align="center" style="background-color:#c8a960;border-radius:4px;padding:14px 28px;">
<a href="{SERVER_URL}/rsvp?email=*|URL:EMAIL|*&choice=yes" target="_blank" style="font-family:Georgia,'Times New Roman',serif;font-size:15px;font-weight:bold;color:#0a1628;text-decoration:none;text-transform:uppercase;letter-spacing:2px;display:inline-block;">✓ OTT LESZEK</a>
</td>

<td width="16">&nbsp;</td>

<!-- NO button -->
<td align="center" style="background-color:#2a2a3a;border-radius:4px;padding:14px 28px;">
<a href="{SERVER_URL}/rsvp?email=*|URL:EMAIL|*&choice=no" target="_blank" style="font-family:Georgia,'Times New Roman',serif;font-size:15px;font-weight:bold;color:#c8a960;text-decoration:none;text-transform:uppercase;letter-spacing:2px;display:inline-block;">✗ NEM TUDOK JÖNNI</a>
</td>

</tr></table>
</td></tr>
</table>

<!-- Text Content (for spam score) -->
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;margin-top:20px;">
<tr><td style="padding:0 20px;">
<div style="color:#c8a960;font-family:Georgia,'Times New Roman',serif;font-size:13px;line-height:1.8;text-align:center;padding:20px;background-color:#0d1f3c;border:1px solid #1a3350;">
<p style="font-size:18px;font-weight:bold;text-transform:uppercase;letter-spacing:3px;margin:0 0 20px;">NOBU BUDAPEST</p>
<p style="font-size:16px;font-weight:bold;text-transform:uppercase;letter-spacing:2px;margin:0 0 20px;">OPENING DINNER</p>
<p style="margin:0 0 15px;">ÖRÖMMEL MEGHÍVJUK ÖNT ÉS PARTNERÉT<br>A MEGÚJULT NOBU BUDAPEST EXKLUZÍV<br>NYITÓESEMÉNYÉRE.</p>
<p style="margin:0 0 15px;">AZ ESEMÉNYEN SZEMÉLYES JELENLÉTÉVEL<br>MEGTISZTEL MINKET: NOBU MATSUHISA.</p>
<p style="margin:0 0 5px;font-weight:bold;">IDŐPONT: {date_text}.</p>
<p style="margin:0 0 5px;">ÉRKEZÉS: 19:00</p>
<p style="margin:0 0 15px;">KEZDÉS: 19:30</p>
<p style="margin:0 0 5px;font-weight:bold;">HELYSZÍN: NOBU BUDAPEST</p>
<p style="margin:0 0 5px;">KEMPINSKI HOTEL CORVINUS BUDAPEST,</p>
<p style="margin:0 0 5px;">1051 BUDAPEST, ERZSÉBET TÉR 7-8.</p>
<p style="margin:20px 0 0;font-weight:bold;">RSVP: INFO@NOBURESTAURANT.HU</p>
</div>
</td></tr>

<!-- Footer -->
<tr><td align="center" style="padding:15px 20px;color:#4a5a7a;font-family:Arial,sans-serif;font-size:11px;">
<p style="margin:0 0 5px;">NOBU Budapest | Kempinski Hotel Corvinus Budapest</p>
<p style="margin:0 0 5px;">1051 Budapest, Erzsébet tér 7-8. | info@noburestaurant.hu</p>
<p style="margin:15px 0 0;color:#3a4a6a;">Azért kapta ezt az emailt, mert feliratkozott a NOBU Budapest hírlevelére.<br>
<a href="*|UNSUB|*" style="color:#5a6a8a;">Leiratkozás</a></p>
</td></tr>
</table>

</td></tr></table>
</body></html>"""

plain = f"""NOBU BUDAPEST - OPENING DINNER

ÖRÖMMEL MEGHÍVJUK ÖNT ÉS PARTNERÉT
A MEGÚJULT NOBU BUDAPEST EXKLUZÍV NYITÓESEMÉNYÉRE.

AZ ESEMÉNYEN SZEMÉLYES JELENLÉTÉVEL MEGTISZTEL MINKET: NOBU MATSUHISA.

IDŐPONT: {date_text}.
ÉRKEZÉS: 19:00 | KEZDÉS: 19:30

HELYSZÍN: NOBU BUDAPEST
KEMPINSKI HOTEL CORVINUS BUDAPEST
1051 BUDAPEST, ERZSÉBET TÉR 7-8.

OTT LESZEK: {SERVER_URL}/rsvp?email=*|URL:EMAIL|*&choice=yes
NEM TUDOK JÖNNI: {SERVER_URL}/rsvp?email=*|URL:EMAIL|*&choice=no
"""

print(f"═══════════════════════════════════════════════════════")
print(f"  NOBU {day_label.upper()}I AUTOMATION EMAIL")
print(f"═══════════════════════════════════════════════════════")
print(f"")
print(f"Subject: {subject}")
print(f"Preview: {preview}")
print(f"")
print(f"─── HTML (másold be a Mailchimp HTML editor-ba) ───")
print(html)
print(f"")
print(f"─── PLAIN TEXT ───")
print(plain)
print(f"")
print(f"✅ Kimenet mentése: python3 generate_automation_email.py {day} > {day}_email.html")
