# NOBU Customer Journey Setup — Mailchimp UI

**Cél:** Amikor a szerver felírat egy vendéget a megfelelő tag-gel (`szerda` vagy `csutortok`), 
a Mailchimp automatikusan kiküldje a megfelelő napi meghívó emailt.

**Egy darab** Customer Journey kell, ami mindkét napot kezeli.

---

## Lépések

### 1. Nyisd meg a Customer Journey buildert
Mailchimp → **Automations** → **Customer Journeys** → **Create Journey**

Válaszd: **"Start from scratch"**

### 2. Nevezd el
**Journey name:** `NOBU Opening Dinner – Meghívó`

### 3. Válaszd ki a listát
**Audience:** `Buno Kft.` (7b625dbadf)

### 4. Trigger beállítása
A kezdőpont (trigger) automatikusan "Contact signs up" — ez jó.

### 5. Szűrés tag-re (Rule/Feltétel hozzáadása)
Kattints a **"+"** gombra a trigger után → válaszd: **"If/Else"** (feltétel)

- **Feltétel 1:** Tag → is → `szerda`
- **Feltétel 2:** Tag → is → `csutortok`
- **Else:** Exit journey (alapértelmezett)

### 6. Szerdai email
Az "If tag = szerda" ágon kattints **"+"** → **"Send email"**

- **Campaign name:** `NOBU Szerdai meghívó`
- **Email subject:** `NOBU Budapest Opening Dinner – Meghívó | 2026. MÁJUS 27.`
- **Preview text:** `Örömmel meghívjuk Önt és partnerét a megújult Nobu Budapest exkluzív nyitóeseményére.`
- **From name:** `NOBU Budapest`
- **From email:** `news@noburestaurant.hu`

**Email tartalom:** Másold be a `szerda_automation_email.html` teljes tartalmát 
(vagy futtasd: `python3 generate_automation_email.py szerda` és másold ki a HTML részt)

- **Send timing:** Immediately (ne legyen delay)

### 7. Csütörtöki email
Az "If tag = csutortok" ágon kattints **"+"** → **"Send email"**

- **Campaign name:** `NOBU Csütörtöki meghívó`
- **Email subject:** `NOBU Budapest Opening Dinner – Meghívó | 2026. MÁJUS 28.`
- Minden más ugyanaz, csak a `csutortok_automation_email.html` tartalmát másold be

### 8. Indítás
- **Send test email** mindkét email-re (ellenőrzés)
- Kattints: **"Start Sending"** (jobb felső sarok)

---

## Működés

```
Rendezvényszervező megnyitja a linket
  → Beírja vendég nevét + emailjét
  → Szerver feliratja a vendéget Mailchimp listára
    - Tag: "szerda" vagy "csutortok"
    - NAP mező beállítva
  → Customer Journey trigger: contact signs up
  → If/Else: tag = "szerda"? → Szerdai email azonnal kimegy
  → If/Else: tag = "csutortok"? → Csütörtöki email azonnal kimegy
  → Az emailben RSVP gombok → vendég visszajelez
```

---

## Linkek

| Feliratkozó oldal | URL |
|---|---|
| Szerda (május 27.) | `https://nobu-rsvp.onrender.com/subscribe/szerda` |
| Csütörtök (május 28.) | `https://nobu-rsvp.onrender.com/subscribe/csutortok` |

---

## Tesztelés

1. Nyisd meg a szerdai linket
2. Írj be egy teszt email címet (pl. sajátod)
3. Kattints "Meghívó küldése"
4. Ellenőrizd: megjött a szerdai meghívó email?
5. Kattints az "OTT LESZEK" gombra az emailben
6. Ellenőrizd Mailchimp-ben: a kontakt RSVP mezője frissült?
