# Appen i App Store och Google Play

*Hur webbappen blir appar i butikerna med Capacitor, vad bygget i GitHub Actions gör, vilka nycklar det behöver och vad som ännu inte är provat.*

<!-- toc -->
## Innehåll

- [Skalet](#skalet)
- [Bygget i GitHub Actions](#bygget-i-github-actions)
  - [Nycklarna](#nycklarna)
- [Butikssidorna](#butikssidorna)
  - [Skärmbilderna](#skärmbilderna)
- [Det som inte är provat](#det-som-inte-är-provat)
<!-- /toc -->

## Skalet

Appen i butikerna är samma webbapp som på GitHub Pages ([Appen för idrottaren](../app.md)), inpackad med Capacitor. Inställningarna står i `app/capacitor.config.ts`:

- appens id är `io.github.generaltgul.troskel` och namnet *Tröskel*,
- appen läser bara sina egna filer (`dist/`), utan någon server-adress, så inget lämnar telefonen,
- de inbyggda projekten ligger i `app/android` (Gradle) och `app/ios` (Swift Package Manager, inga CocoaPods).

Insticken är fem:

- `@capacitor/app` (MIT), som bara på Android läser bakåtknappen: den stänger det som är öppet, och annars läggs appen i bakgrunden ([Vad appen gör](../app.md#vad-appen-gör)),
- `@capacitor/preferences`, som sparar schemat, tiderna och inställningarna i telefonens egen lagring i stället för webbvyns ([Integritet](../app.md#integritet)),
- `@capacitor/filesystem` och `@capacitor/share` (MIT), som sparar en fil ur appen: kalenderfilen, kopian och schemat till coachen ([Spara en fil](../app.md#spara-en-fil)),
- `@capgo/capacitor-health` (MPL-2.0), som bara på Android läser Health Connect: pass, puls, sömn, vilopuls och HRV, aldrig skrivning ([Health Connect i Android-appen](../app.md#health-connect-i-android-appen)). Därför kräver Android 8 (API 26). Innan en version med hälsobehörigheterna får publiceras på Google Play ska behörigheterna deklareras i Play Console (*App content → Health apps* och datasäkerheten), med integritetspolicyns adress. *ej verifierat mot källa*

Ikonen och startbilden ritas ur källorna i `app/resources`.

Kameran får appen bara för att läsa QR-koden med schemat. På Android krävs ingen kamera för att installera appen, och på iOS står skälet i `Info.plist`.

Bygget för butikerna görs med relativ bas, eftersom appen inte ligger under `/troskel/app/`:

```bash
cd app
TROSKEL_BASE=./ npx vite build --outDir dist
npx cap sync            # kopierar dist/ in i de inbyggda projekten
```

## Bygget i GitHub Actions

Arbetsflödet `.github/workflows/app.yml` körs för hand (*Actions → App → Run workflow*) och på taggar som `app-v1.2.3`. Taggen ger versionen; annars gäller versionen i `app/package.json`. Byggnumret är körningens nummer.

| Jobb | Vad det gör | Behöver |
|---|---|---|
| *Webb (prov och bygge)* | Vitest, bygget för GitHub Pages och bygget för butikerna | inget |
| *Publicera på GitHub Pages* | lägger ut appen på `generaltgul.github.io/troskel/app/`, bara från `main` | att Pages är påslaget med GitHub Actions som källa |
| *Android (APK och AAB)* | en APK för att prova och en AAB för Play; från `main` läggs APK:n också på den fasta länken i releasen `android-test` ([Appen utan butiken](../app.md#appen-utan-butiken)) | nyckeln för signerad AAB |
| *iOS (simulatorn, utan signering)* | bygger appen för simulatorn som kontroll | inget |
| *iOS till TestFlight* | arkiv signerat med App Store Connect-nyckeln, uppladdat till TestFlight | App Store Connect-nyckeln; jobbet hoppar över sig självt utan den |
| *Skärmbilder till butikerna* | skärmbilderna nedan, när rutan *Ta skärmbilderna* är ikryssad | inget |

Uppladdningen till Play (internt test, som utkast) och till TestFlight sker på en tagg eller när rutan *Ladda upp* är ikryssad.

### Nycklarna

Nycklarna ligger som hemligheter i repot på GitHub (*Settings → Secrets and variables → Actions*), aldrig i filerna. `.gitignore` stoppar nyckelfiler som `*.jks`, `*.p8` och `*.p12`.

| Hemlighet | Vad |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | uppladdningsnyckeln för Play, base64-kodad |
| `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | lösenorden och namnet på nyckeln |
| `PLAY_SERVICE_ACCOUNT_JSON` | tjänstekontot som får ladda upp till Play |
| `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8_BASE64` | App Store Connect-nyckeln (rollen Admin, så att Xcode kan göra certifikatet och profilen själv) |
| `APPLE_TEAM_ID` | utvecklarkontots team |

Utan nyckeln för Android blir AAB-filen osignerad, och bygget säger det. Play tar emot en AAB genom API:et först när den första är uppladdad för hand i Play Console.

## Butikssidorna

Appen ligger i butikerna med en privatperson som säljare. Texterna till butikssidorna står i `app/store`, på svenska och engelska:

- `app/store/android/<språk>/`: titeln, den korta och den långa beskrivningen för Google Play,
- `app/store/ios/<språk>/`: namnet, underrubriken, beskrivningen, reklamtexten, sökorden och adresserna till integritetspolicyn och supporten för App Store.

Integritetspolicyn är `app/public/privacy.html`, som följer med bygget och ligger på `generaltgul.github.io/troskel/app/privacy.html`. Den säger samma sak som [Integritet](../app.md#integritet): appen samlar inte in något. Ändras det appen sparar eller skickar ändras policyn och dess datum i samma vända.

### Skärmbilderna

`app/store/screenshots.mjs` fotograferar appen med exempelschemat, på svenska och engelska, i varje storlek som butikerna vill ha: iPhone 6,9 och 6,5 tum, iPad 13 tum, Android-telefon och surfplattor på 7 och 10 tum. Klockan står still på torsdagen i exempelschemats första vecka, en dag med ett kvalitetspass, så att första bilden visar ett pass och bilderna blir likadana varje gång. Bilderna läggs i `app/store/screenshots/`, som git ignorerar: de laddas upp i butikerna, inte i repot.

Exempelschemat (`app/public/demo.troskel`) är svenskt. I de engelska bilderna byter skriptet därför exempelidrottarens, loppens och testets namn mot engelska (`NAMES_EN` i skriptet). Får exempelschemat ett nytt namn läggs det till där.

Skriptet kontrollerar också varje vy det fotograferar: menyns namn ska stå på en rad, och ingen rubrik, inget mål och ingen siffra får sticka ut ur sitt kort eller utanför skärmen. Ett fel avbryter körningen med en lista. `node store/screenshots.mjs --check` gör bara kontrollen, utan bilder, och provar dessutom en telefon på 320 punkter och en med större text.

```bash
cd app
npm i --no-save playwright@1.65.1 && npx playwright install chromium
TROSKEL_BASE=./ npx vite build && node store/screenshots.mjs
```

På en maskin där Playwrights Chromium inte går att ladda ned pekar `CHROMIUM_PATH` på en Chromium som redan finns, till exempel en äldre i `~/.cache/ms-playwright`. Ligger Playwright i en annan katalog än appens `node_modules` pekar `PLAYWRIGHT_PATH` på paketet (`…/node_modules/playwright`). Med ett minnestak, på en maskin som delas:

```bash
CHROMIUM_PATH=~/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell \
TROSKEL_BASE=./ systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 node store/screenshots.mjs
```

Skriptet startar Vite direkt, inte genom `npx`, och stoppar det när bilderna är tagna, så att porten 4179 blir fri.

## Det som inte är provat

Bygget är provat i GitHub Actions, men appen har inte körts i en riktig telefon. Proven står i [PROV.md](../../PROV.md#appen-i-telefonen).
