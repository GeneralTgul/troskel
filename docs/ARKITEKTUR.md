# Arkitektur

*Lopanalys är ett Windows-program för löpanalys och fystester på löpband, och också för tester i watt på cykeltrainer och Concept2 SkiErg, gjort för att användas av coacher. Målet är ett riktigt program där coachen väljer en kamera som källa (till exempel en GoPro), gör proffsiga tester, ser allt på skärmen och sedan skriver ut rapporten.*

<!-- toc -->
## Innehåll

- [Vad programmet gör](#vad-programmet-gör)
- [Teknik](#teknik)
- [Modulerna](#modulerna)
- [Två kameror och telefonen som kamera](#två-kameror-och-telefonen-som-kamera)
- [Bikefit och stakteknik](#bikefit-och-stakteknik)
- [Testerna och det som inte går att prova i utvecklingsmiljön](#testerna-och-det-som-inte-går-att-prova-i-utvecklingsmiljön)
<!-- /toc -->

## Vad programmet gör

1. **Kameran:** coachen väljer en källa: en webbkamera, ett inspelningskort, en GoPro över USB, telefonen eller en IP-kamera över nätverket, eller en videofil från kortet. En andra kamera kan filma samtidigt från ett annat håll.
2. **Livevyn:** visas helskärm på TV:n med livebilden (eller båda bilderna), skelettet och siffrorna:
   - kadens, översteg, underbenets vinkel, knäböjningen, bålens lutning, studsen och höftfallet,
   - puls, fart, lutning och steget i testet.
3. **Sporten väljs per test:** löpning på band, cykel på en trainer eller Concept2 SkiErg. Den bestämmer testerna, enheten för belastningen (km/h eller watt) och vilken ergometer som ansluts. Maxpuls, trösklar och zoner hålls isär per sport.
4. **Löpningens tester styr löpbandet:**
   - **Löpteknik:** 2–3 farter med 120 s vardera, så att steget hinner sätta sig efter fartbytet.
   - **Laktattrappa:** steg på 4 min, och pausen för blodprovet efter varje steg. Laktatvärdet skrivs in i programmet och ger LT1, LT2 och zonerna.
   - **Maxpuls:** en trappa till utmattning.
   - **Puls- och fartzoner:** för den som saknar laktatmätare. 4–5 steg på 4 min i jämn fart utan pauser och prov, direkt följda av en fartstege till max (0,5 km/h per minut), på 1 % lutning hela testet. Stegen ger sambandet mellan puls och fart, trappan ger maxpulsen, och zonerna får en fart som märks *Fart ur puls, inte ur laktat*. Testet går av sig självt: stoppas bandet på panelen avslutas det.
5. **Testerna i watt:**
   - **Cykel:** laktattrappa med steg på 5 min, ramptest (maxpuls, bästa minuteffekt och FTP) och FTP 20 minuter. Trainern håller effekten över Bluetooth (FTMS, ERG-läge).
   - **SkiErg:** laktattrappa med steg på 4 min och ramptest. En SkiErg går inte att styra: TV:n visar målet, monitorn (PM5) läses över Bluetooth, och stegets belastning är den uppmätta medeleffekten. Testet läggs också in som ett pass på monitorn, så att steget och målet syns framför åkaren.
   - **Bikefit och stakteknik:** egna tester som filmas: uppvärmning utan film, sedan ett steg i jämn arbetseffekt som filmas och analyseras efteråt. Bikefit filmas från sidan och framifrån, stakteknik från sidan. Se [Bikefit och stakteknik](#bikefit-och-stakteknik).
6. **Styrningen av bandet** går på ett av tre sätt:
   - FTMS över Bluetooth,
   - QZ (appen som många redan har mellan bandet och Zwift),
   - manuellt: programmet visar då "Sätt bandet på 12,0 km/h" och väntar på operatören.
7. **Rapporten** skrivs ut på skrivaren, sparas som PDF eller mejlas till idrottaren:
   - stillbilder där felen är inringade, med vinklar, förklaring och övning,
   - laktatkurvan, zonerna och jämförelsen med förra testet,
   - coachens logga och namn.
   - Bikefit och stakteknik har egna sektioner, se [Bikefit och stakteknik](#bikefit-och-stakteknik).
   - Testet sparas också som FIT-fil för Strava och Garmin Connect, och den följer med i mejlet.
8. **Zonerna i flera system:** samma trösklar visas som 3 zoner (Seiler), svenska A1–A3+, Olympiatoppens I1–I5, Garmins och Polars fem zoner eller Friels sju. Rutan *Fyll i din klocka* ger värdena till Garmin Connect, med zon 2 upp till LT1 och zon 4 upp till LT2. Utan laktattrösklar räknas zonerna ur maxpulsen och märks *Uppskattade ur maxpuls*. Efter *Puls- och fartzoner* får de också en fart ur pulsen.
9. **Uppskattat VO2max:** räknas ur testets fart, lutning och puls och visas alltid med intervall och metod. Det ersätter inte ett test med andningsgaser.
10. **Sidan Testerna:** vad varje test mäter, enligt vilken standard och vad man får fram, en sport i taget (`assessments/catalog.py`, `SPORT_TESTS`), med utskriften *Om testerna* på en A4 per sport.
11. **Träningsschemat:** en plan byggd på testet, tillgänglig tid och mål, som PDF och kalenderfil. Sidan *Träningsschema* öppnas från idrottaren eller från ett resultat, schemat sparas per idrottare, och efter ett nytt test läggs resten av schemat om efter de nya zonerna. Se [handboken](handbok/traningsschema/traningsschema.md) och [Träningsplanen](TRANINGSPLAN.md).

## Teknik

| Del | Val | Varför |
|---|---|---|
| Språk | Python 3.12+ | MediaPipe, numpy och scipy finns där; ett språk i hela programmet |
| Gränssnitt | PySide6 (Qt 6, LGPL) | Riktigt skrivbordsprogram med helskärm på TV, utskrift, PDF; LGPL tillåter försäljning med dynamisk länkning |
| Pose | MediaPipe Pose Landmarker (Apache-2.0), 33 punkter med häl och tå | Går på en vanlig bärbar processor i realtid |
| Kamera | OpenCV (Apache-2.0) + GoPros HTTP/UDP över USB | Alla vanliga kameror; GoPro HERO8+ utan GoPros program |
| Bluetooth | bleak (MIT) | FTMS-band och pulsband på Windows (WinRT) |
| Rapport | Jinja2-mallar → HTML → PDF med Qt WebEngine, diagram med matplotlib (SVG) | Proffsig typografi och layout med CSS, samma HTML för förhandsvisning och utskrift |
| Lagring | SQLite i `%APPDATA%\Lopanalys` | En fil per coach, enkel backup |
| Kryptering | cryptography (Apache-2.0/BSD) | Säkerhetskopian: AES-256-GCM med nyckel ur scrypt |
| FIT-fil | Egen kodare i ren Python (`share/fit.py`) | Inget nytt beroende att bygga in i exe-filen. Formatet är öppet, och filen läses tillbaka i proven |
| E-post | Pythons `smtplib` mot coachens eget SMTP-konto | Ingen egen mejltjänst och inget biträde utöver coachens e-postleverantör. Lösenordet skyddas med Windows dataskydd (DPAPI) genom `ctypes` |
| Paketering | PyInstaller i GitHub Actions (Windows) | En installerbar exe |

**Licenserna:** programmet är AGPL-3.0-or-later ([LICENSE](../LICENSE)). Beroendena har tillåtande licenser, utom PySide6 (LGPL-3.0, dynamiskt länkat); licensen står vid varje beroende i `pyproject.toml`. QZ används bara som separat program via dess websocket, och ingen av dess kod finns här.

## Modulerna

```
src/lopanalys/
  core/         bus.py (pub/sub), models.py (Athlete, Session, StepRecord), sport.py (Sport och sportprofilerna),
                i18n.py, paths.py
  camera/       base.py (FrameSource, SourceInfo), discovery.py, opencv_source.py, gopro.py, file_source.py, recorder.py,
                lease.py (en öppnare per kamera), network.py (telefonen och nätverkskameror: MJPEG, adresser, mDNS, mätning),
                network_sim.py och gopro_sim.py (låtsade kameror för proven)
  pose/         types.py, estimator.py, models.py (hämtar .task), filters.py, gait.py, metrics.py, findings.py, annotate.py, live.py,
                multiview.py (två vyer: stegen paras på den gemensamma klockan, och den andra vyns bildtakt),
                reliability.py (vilka värden bildtakten bär: visas, märks osäkra eller utelämnas),
                technique.py (det bikefit och stakteknik delar: resultatet, riktvärden med grund och mätfel, jämförelsen),
                technique_draw.py (nyckelbilderna), technique_synth.py (brus och film för de syntetiska idrottarna),
                bike/ (cycle.py pedalvarvet, analysis.py mätvärden och förslag, checklist.py, draw.py, synthetic.py),
                ski/ (strokes.py draget, analysis.py, draw.py, synthetic.py)
  devices/      base.py (Treadmill, Ergometer, HeartRateMonitor), ftms.py, heart_rate.py, qz.py, manual.py, sim.py, manager.py,
                ftms_bike.py (cykeltrainer: Indoor Bike Data och Set Target Power), pm5.py (Concept2 PM5: belastningen läses, testet läggs in som pass på monitorn), csafe.py (CSAFE-ramarna för passet)
  assessments/  protocol.py (också regeln för löpteknik, gait_compatible), library.py (färdiga protokoll),
                library_power.py (protokollen i watt för cykel och SkiErg), power.py (FTP, bästa minuteffekt, W/kg),
                erg_values.py (medeleffekt för hand och SkiErgens dragfaktor i `session.result`),
                runner.py, lactate.py, zones.py, max_hr.py,
                hr_zones.py (Puls- och fartzoner: stabil puls per steg, sambandet puls–fart och zonernas fart),
                zone_systems.py (zonsystemen som data, omräkning och Garmin-värden), vo2max.py (uppskattat VO2max),
                catalog.py (texterna om testerna), cadence.py (pulsbandets kadens före kamerans),
                css.py (simtestet: CSS ur 400 m och 200 m, sparas som ett manuellt test; formuläret är ui/views/css_test.py)
  plan/         models.py, library.py (passbiblioteket), periodize.py (block och veckor), engine.py (build_plan),
                resolve.py (zon till puls och fart, omtest), text.py, ics.py (kalenderfil),
                codec.py (schemat som JSON för lagringen), demo.py (exempelschema)
  storage/      db.py (SQLite, schema och migreringar, radering, lagringstider),
                privacy.py (krypterad säkerhetskopia, utdrag, flytt av datamappen, loggens ålder)
  report/       builder.py, charts.py, pdf.py, technique.py (sektionerna för bikefit och stakteknik),
                plan_builder.py (träningsschemats utskrift), templates/, assets/
  share/        fit.py (testet som FIT-aktivitet, och en läsare för kontroll),
                fit_read.py (klockans FIT-fil: löpdynamiken från HRM-Pro+, för kontrollen av löptekniken), mail.py (SMTP, mallen för hälsningen),
                secret.py (lösenordet skyddat: DPAPI på Windows, nyckelfil annars)
  ui/           app.py, main_window.py, lock.py (PIN-kod och lås efter vila),
                views/ (idrottare, test, live, laktat, rapport, träningsschema, testerna, inställningar, send_mail.py), widgets/
  i18n/         <modul>.sv.json, <modul>.en.json
```

**Reglerna:**
- **Ingen Qt utanför `ui/` och `report/pdf.py`.** camera, pose, devices, assessments, plan, storage och share går att köra och testa utan skärm. Exempelkommandot `plan/demo.py` anropar `report/pdf.py` för PDF:en.
- **Sporten:** varje test har en sport (`Session.sport`), och `core/sport.py` säger vad sporten betyder: belastningens storhet och enhet (fart i km/h för löpning, effekt i watt för cykel och SkiErg), tempot (min/km eller per 500 m), vilka enhetslägen som styr belastningen och skalan körningen och analysen räknar med. Belastningen läses som `load` i ny kod, med enheten ur `profile(sport)`; fälten bakom heter fortfarande `speed_kmh` och bär watt i de andra sporterna. Databasen fick sporten i version 8 genom en migrering som bara lägger till, och träningsschemat i version 9. Vad som är gjort och vad som återstår står i [Flera sporter](sporter/ombyggnad.md#nuläget).
- **Inget flyttas mellan sporter.** Maxpulsen lagras per idrottare och sport (`Athlete.max_hr_for(sport)`), trösklarna räknas ur sportens egna laktattester, och *förra testet*, jämförelsen och den samlade rapporten tar en sport i taget. En sport utan eget test får inga uppskattade zoner ur en annan: resultatet och rapporten säger att testet saknas. Avdragen mellan sporter gick inte att belägga med källa.
- **Tre slags ergometer** (`devices.base.Ergometer`): *styrd* (`controllable`: löpband över FTMS eller QZ, trainer i ERG), *bara mätt* (`measured` utan `controllable`: Concept2 PM5) och *manuell*. Körningen sätter belastningen på den första, visar målet och räknar med det uppmätta på den andra, och väntar på operatören på den tredje. Ett steg med `Stage.free` har inget satt mål alls (FTP-testets 20 minuter).
- **Trösklar räknas alltid på watt i watt-sporterna,** aldrig på tempo: på SkiErg går watten som farten i kubik, och Dmax och log-log hamnar på andra ställen på en tempoaxel.
- **FTP är inte LT2.** FTP räknas i `assessments/power.py` (95 % av bästa 20 minuter, eller 75 % av bästa minuten i ett ramptest) och visas alltid med metoden. Det blandas aldrig med trösklarna ur laktat.
- **Planen lagrar zoner, inte siffror.** Bara `plan/resolve.py` räknar om en zon till puls och fart, ur testet planen bygger på.
- **Ett sparat schema byggs inte om när det läses.** Det ligger i tabellen `plans` som det byggdes (`plan/codec.py`), och motorn körs först när coachen ändrar formuläret och sparar.
- **Bussen:** delarna pratar genom `core.bus.Bus` med ämnena i `bus.py`. UI:t prenumererar och flyttar över till Qt-tråden själv.
- **Trådarna:** varje källa och enhet kör i en egen tråd (Bluetooth med en egen asyncio-loop i tråden), och inget blockerar Qt-tråden.
- **Text:** Swedish first. Every user-visible string goes through `core.i18n.t()` with its own module file in `i18n/`.
- **Engelska i koden.** Identifiers and comments are English. Dokumentationen är på svenska.
- **Ett test görs för det som det mäter.** Ett maxpulstest är för maxpulsen och anpassas inte för löptekniken: det viktigaste är att testerna blir så bra som möjligt, inte att de blir många och halvbra. Ett protokoll ändras därför aldrig för att passa en kombination. Regeln står på ett ställe, `assessments.protocol.gait_compatible`: löpteknik får kombineras med ett protokoll bara när alla steg har jämn fart och oförändrad lutning i minst 60 s (`GAIT_MIN_STAGE_S`). Guiden spärrar *Nästa* annars. Lutningstrappan klarar det aldrig, laktattrappan och *Puls- och fartzoner* gör det.
- **Fart ur puls märks alltid.** Det som räknas i `hr_zones.py` är uppskattat utan laktat. Zonernas fart bär `speed_basis = "hr"`, och en fart utanför de uppmätta stegen `speed_*_extrapolated`. Skärmen och rapporten visar märkningen och ≈.
- **Hälsodata:** inspelningar, databasen och rapporterna committas aldrig.
- **Dataskydd:** filer i datamappen heter efter testets eller idrottarens nummer, aldrig efter namnet, och loggen nämner nummer och filnamn, aldrig personer eller sökvägar. En ny fil som programmet sparar åt coachen registreras med `Database.add_saved_file`, så att raderingen hittar den. Allt står i [DATASKYDD.md](DATASKYDD.md).
- **Mejl:** programmet mejlar bara när coachen trycker på *Skicka*, bara till en idrottare med rutan *Kontakt om uppföljning*, och alltid över en krypterad anslutning till servern (STARTTLS eller TLS, aldrig klartext). Adresser och lösenord skrivs aldrig i loggen, och lösenordet sparas bara genom `share.secret`. Proven skickar till en låtsad server på `127.0.0.1` (`tests/share/fake_smtp.py`), aldrig till en riktig.
- **Migreringar** är rena tillägg (`MIGRATIONS` i `storage/db.py`), och varje ny version provas mot databasen i `tests/storage/fixtures/main_v3.sql`, som är skriven av version 3.

## Två kameror och telefonen som kamera

**Två oberoende vyer.** Ett test kan ha två kameror (`TestPlan.camera` och `camera2`, med hållen `view` och `view2`). Varje kamera har sin egen källa, sin egen pose-kedja (`pose.live.LiveAnalyzer` med egen MediaPipe), sin egen inspelning och sina egna mått. Inget kalibreras mellan kamerorna. Med en kamera skapas inget av det andra, och allt går som förut.

| Del | Första kameran | Andra kameran |
|---|---|---|
| Ämnen på bussen | `gait_step`, `gait_summary` | `gait_step_2`, `gait_summary_2` |
| Liveanalysens bildtakt | Varje bild kameran ger | Högst 15 bilder/s, sänks till lägst 6 när den första inte hinner med |
| Filmen | `session-<id>-<tid>.mp4` (`sessions.video_path`) | `session-<id>-<tid>-2.mp4` (`result["second_video"]`), med varje bild från kameran |
| Analysen efteråt | En rad i `gait_analyses` | En egen rad med `"angle": "second"`, som läses in under nyckeln `second` |
| Värden per steg i testet | `StepRecord.gait` | `StepRecord.gait["second"]` |

**Den gemensamma klockan.** Varje livebild stämplas med `time.perf_counter()` när den kommer in i programmet, oavsett källa. Inspelningarnas sidofiler har samma stämplar, och skillnaden mellan filmernas första bild sparas med testet (`result["second_offset_s"]`). Efter analysen parar `pose.multiview.match_steps` ihop markkontakterna i de två filmerna, fot för fot. Förskjutningen som ger flest par är den andra kamerans eftersläpning (en telefon över wifi ligger efter en GoPro över USB), och den sparas med antalet gemensamma steg (`second["sync"]`). Stämpeln sätts när bilden tas emot, inte när den togs, så kamerans egen fördröjning syns bara som den eftersläpningen.

**Processorn.** Två MediaPipe-kedjor delar på datorn. Den andra vyns liveanalys tar därför den nyaste bilden högst 15 gånger per sekund, och `LiveController._balance` sänker taket var femte sekund när den första vyn analyserar mindre än 80 % av det kameran ger (`pose.multiview.balance_second_rate`). Loggen har en rad per vy (`live[side_left]: camera … fps, analysis … fps`) och en gemensam rad var trettionde sekund (`two views: …`). Analysen av filmerna efteråt går en film i taget.

**Mätt i utvecklingsmiljön** 2026-11-14 (fyra delade kärnor, MediaPipe *full*, två filmer i 640 × 360 och 30 bilder/s som källor):

| Läge | Första vyn | Andra vyn | Processor |
|---|---|---|---|
| En kamera | 28 bilder/s | | 1,2 kärnor |
| Två kameror utan tak | 22 bilder/s | 23 bilder/s | 2,2 kärnor |
| Två kameror, tak 15 | 22 bilder/s | 14 bilder/s | 2,0 kärnor |
| Två kameror, tak 8 | 25 bilder/s | 8 bilder/s | 1,9 kärnor |

Maskinen var belastad av annat under mätningen, så talen visar riktningen och inte vad en vanlig Windows-dator i gymmet klarar. Det provas på riktig hårdvara, se [PROV.md](PROV.md#löptekniken).

**Telefonen och nätverkskameror** öppnas med adress (`camera/network.py`):

- **MJPEG över HTTP** läses av en egen läsare (`MjpegSource`). En tråd tar emot bilderna så fort de kommer och avkodar bara den nyaste hela bilden, så att en skur på wifi inte blir en fördröjning. Svarar adressen med något annat än MJPEG går den till FFmpeg.
- **RTSP** går genom OpenCV och FFmpeg som tidigare (`OpenCvSource`, TCP och liten buffert).
- **Adressen:** `normalize_address` gör om det coachen skrev till adresser att prova, och `probe_address` frågar dem samtidigt. HTTP får en vanlig förfrågan och RTSP en `OPTIONS`, så provet tar bråkdelar av en sekund. En IP-adress utan port provas mot portarna i listan `PHONE_APPS`.
- **mDNS:** `mdns_browse` frågar det lokala nätet efter `_rtsp._tcp` och `_http._tcp` med en egen liten klient, och bara det som verkligen svarar med en ström listas. Frågan går ut på varje nätverkskort, eftersom GoPron över USB också är ett.
- **Mätningen:** varje ström loggar var tionde sekund bildtakt, bildintervallets median och 95:e percentil, bithastighet, överhoppade bilder, återanslutningar och tiden till första bilden (`stream …`). För RTSP jämförs programmets klocka med strömmens egna tidsstämplar (`backlog`), som växer när bilder köar på vägen. Fördröjningen från linsen till programmet går inte att mäta utan en klocka i bilden. Den provas på riktig hårdvara och syns annars som eftersläpningen mot den första kameran.
- **Lösenord** i en adress (`användare:lösenord@…`) sparas med adressen i inställningen `camera.recent`. Etiketter, felmeddelanden och loggrader skrivs utan dem (`camera.base.safe_url`).

Apparna som programmet provar först står i `PHONE_APPS` i `camera/network.py`.

Från terminalen: `python -m lopanalys.camera find` söker med mDNS, `find 192.0.2.50` provar en adress, och `preview <adress> --seconds 30` visar strömmen och skriver mätningen.

## Bikefit och stakteknik

Två kameraanalyser utöver löptekniken, för landsvägscykel på trainern och för stakning på Concept2 SkiErg. De går samma väg som löpningen: poser ur filmen, rensning, händelser, mätvärden, fynd, nyckelbilder. Händelserna är andra, så varje sport har ett eget paket under `pose/`.

| | Bikefit (`pose/bike/`) | Stakteknik (`pose/ski/`) |
|---|---|---|
| Händelsen | Pedalvarvet: en cirkel passas till den närmaste fotledens bana, och vevvinkeln är riktningen från mitten till fotleden. Rotationens riktning ger också åt vilket håll cyklisten är vänd. Framifrån går varvet mellan knäts högsta punkter | Draget: den närmaste handledens höjd. Start och slut tas där handens fart nedåt går mot noll (farten följs till 10 och 30 % av toppfarten, och linjen dras ut till noll) |
| Vyer | Från sidan och framifrån, en analys per film | Från sidan |
| Syntetisk idrottare | `bike/synthetic.py`: sadelhöjden är fri parameter, `truth()` ger de sanna vinklarna | `ski/synthetic.py`: ställning i start och slut, ordningen bål–armar och rytmen |

**Riktvärdena bär sin grund.** Varje mätvärde är en `technique.Ref` med intervall, grund (`research`, `experience` eller `none`), källa och mätfel. Reglerna står på ett ställe, `Ref.classify` och `technique.make_finding`:

- **Bara det som går att mäta bedöms.** Fotleden och knä över pedalaxel har grunden `none` och visas utan färg.
- **Ett erfarenhetsvärde i bikefit blir aldrig rött** (`max_severity = WATCH`). Bara knävinkeln i nedersta läget är forskningsbaserad.
- **En avvikelse som är mindre än mätfelet** blir som mest *Håll koll*, får meningen om mätfelet och ger i bikefit ett förslag utan millimetertal (`raise_uncertain`).
- **Kvaliteten** (`quality.level`) är `good`, `uncertain` eller `poor`. Vid `poor` (för få varv eller drag, banan ingen cirkel, personen syns i under 60 % av bilderna) ges inga fynd, inga förslag och inga nyckelbilder.
- **Sadelhöjden i mm** räknas geometriskt ur knävinkeln och benets längd (`saddle_mm_per_deg`), och provet kontrollerar att förslaget verkligen tar den syntetiska cyklisten in i intervallet.

**Lagringen** använder tabellen `gait_analyses` utan migrering: varje film är en rad vars data bär `"kind": "bikefit"` eller `"ski"`. `Database.gait_analysis` hoppar över de raderna, och `Database.technique_analysis` ger `{"kind", "views"}`. Checklistan ligger i testets resultat (`result["bikefit_checklist"]`). Jämförelsen räknas när filmen analyseras, mot idrottarens senaste analys av samma slag och håll (`Store.previous_technique`, `technique.compare`).

**Gränssnittet och rapporten** bygger samma kontext (`report/technique.py`): fliken på resultatsidan (`ui/views/technique_panel.py`) och mallen `templates/technique.html.j2`. Texterna har en egen fil, `i18n/technique.*.json`.

**Testtyperna i guiden.** `TestKind.BIKEFIT` (`"bikefit"`) och `TestKind.SKI_TECHNIQUE` (`"ski"`), samma värden som `pose.technique.KINDS`. I `ui/context.py` heter de `TECHNIQUE_KINDS`, skilda från löpningens `GAIT_KINDS`:

- **Protokollen** (`library_power.bike_fit` och `skierg_technique`): uppvärmning (5 min, 70 % av arbetseffekten) som inte filmas, sedan ett steg *Filmning* i jämn arbetseffekt (cykel 150 W i 3 min, SkiErg 120 W i 2 min). Coachen ändrar effekten, längden och uppvärmningen i guiden. Inga blodprov och inga löpvärden per steg.
- **Kamerasteget** visas alltid. Vyerna följer testet (`KIND_VIEWS` i `new_test.py`): bikefit från vänster eller höger och en andra kamera framifrån, stakteknik från sidan utan andra kamera. *Nästa* är grå utan kamera. En kamera som ger under 60 bilder/s får en varning, och listan *Innan start* säger till när bikefit saknar kameran framifrån. Ramen i kontrollsteget är bredare för en cyklist från sidan (`guide_box("bike_side")`).
- **Inspelningen** (`live_controller._RecorderThread`): varje bild kameran ger (`raw`), med tätare avläsning för 120 bilder/s, och först från det första steget efter uppvärmningen (`gated`, `_arm_film`). Utan samtycke till film startar testet inte, eftersom inget går att mäta utan filmen.
- **TV:n** visar bilden, skelettet, watt, puls och kadens, och i stället för löpningens brickor testets egna värden live (`pose/technique_live.py`). `LiveAnalyzer` med `technique="bikefit"` eller `"ski"` kör varje sekund fönstret (12 s för cykel, 20 s för SkiErg) genom samma `analyze_core` som efter testet, och `summarize` tar medianen av varje värde över de sex senaste varven eller dragen, eller None under tre. Värdena går på `TOPIC_TECH_SUMMARY` (`_2` för den andra kameran) till `LiveController.tech_changed` och brickorna (`GaitTile`) på `LiveBoard`, med riktvärdet och nivån ur analysens `Ref`. Inga egna formler och inget sparas: resultatet är analysen av hela filmen. Demoläget kör samma kedja på democyklistens och demoåkarens poser.
- **Efter testet** analyseras filmerna en i taget (`ResultsView.analyze_technique` → `services.analyze_technique`), med förra testet av samma slag och håll som jämförelse, och sparas som en rad var. Resultatsidan visar bara fliken *Bikefit* eller *Stakteknik* (och *Rapport*): *Laktat och zoner* och *Löpteknik* döljs.
- **Demoläget** har en democyklist (från sidan och framifrån) och en demoåkare ur de syntetiska idrottarna (`ui/demo.py`). Analysen efter ett demotest går på en film av samma idrottare med de poser den ritades ur, eftersom utvecklingsmiljön saknar posemodellen.

**Granska en film** (också utan kamera och modell, med de syntetiska idrottarna):

```bash
python tools/granska_analys.py FILM --sport bike --height 180 --view side_left
python tools/granska_analys.py FILM --sport ski --height 180
python tools/granska_analys.py --sport bike --synthetic --height 178 --out /tmp/granskning
```

Verktyget skriver `cycles.csv` (en rad per varv eller drag), `summary.txt` (värden med grund och mätfel, fynd, förslag, varningar) och blad med bilderna runt varje nedersta och översta läge respektive dragets start och slut.

## Testerna och det som inte går att prova i utvecklingsmiljön

- Kör `pytest` med `source scripts/headless_env.sh` (Linux utan skärm).
- **Simulatorerna** (`devices/sim.py`, `camera/file_source.py`) gör att ett helt test går att köra utan hårdvara. `SimBike` håller effekten i ERG och tappar den under kadens 50, `SimSkiErg` följer målet som visas med en liten avvikelse, och båda slutar när målet går över vad idrottaren orkar, så att en ramp avslutas av sig själv. Programmet har ett demoläge som använder dem, med en demolöpare från sidan och en bakifrån.
- **Låtsade nätverkskameror** (`camera/network_sim.py`): en telefon som sänder MJPEG och kan tappa strömmen, en RTSP-port och en mDNS-svarare. Proven kör två källor samtidigt med olika bildtakt och en som tappar.
- **Det som provas skarpt på riktig hårdvara** (en Windows-dator i gymmet) står i [PROV.md](PROV.md): GoPron över USB, telefonen som kamera, två kameror samtidigt, bandet över FTMS och QZ, cykeltrainern och SkiErgens PM5 (ingen av dem har mött koden än), pulsbandet, MediaPipe-modellen, utskriften, helskärm på TV:n, mejlet från ett riktigt konto och FIT-filen i Strava och Garmin Connect.
- **FIT-filen kontrolleras också av en fristående läsare** (`fitdecode`, bara i utvecklingsmiljön: `pip install -e .[dev]`). Provet hoppas över där paketet saknas.
