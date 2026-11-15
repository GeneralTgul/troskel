# Enheterna för cykel och SkiErg

*Underlag för fler sporter, skrivet 2026-11-14. Enheterna är byggda sedan dess (`devices/ftms_bike.py`, `pm5.py`, simulatorerna, se [Nuläget](ombyggnad.md#nuläget)), men inget har mött riktig hårdvara: proven står i [PROV.md](../PROV.md#cykeltrainern). Det som byggdes annorlunda än förslaget står i [avsnitt 5](#5-förslag-på-klasser-och-gränssnitt). Sidan säger vilken utrustning programmet provas mot, hur en cykeltrainer och en Concept2 PM5 pratar över Bluetooth, vad som kan delas med dagens `devices/`, och vilka prov som behövs på riktig hårdvara.*

**Så här ska uppgifterna läsas:**
- **FTMS och Cycling Power** är skrivna ur minnet av Bluetooth SIG:s specifikationer, samma källa som `ftms.py` bygger på. En webbsökning bekräftade några delar (märkta *(läsaren)*), ur andrahandskällor. Resten är *ej verifierat mot källa* och kontrolleras mot specifikationen när koden skrivs.
- **PM5** bygger på sökningar (2026-11-14), som mest fann forum och GitHub-projekt. Det som bekräftades i en sökning är märkt *(läsaren)*. Resten är ur minnet och märkt *ej verifierat mot källa*. Concept2:s egen specifikation (`PM5_BluetoothSmartInterfaceDefinition.pdf`) gick inte att läsa.
- **Kontrollerat mot källa 2026-11-14** genom webbsökningar och källkod på github.com: Tacx NEO 2T (antal anslutningar), PM5:s UUID:er (ur `pm5-ble.js` i ergarcade/pm5-base) och pycyclings licens. Utfallet står i avsnitt 2 och 3 och märks *(belagt)*.
- **QZ** är bara det som står i `devices/qz.py`. QZ:s källkod är inte läst.

<!-- toc -->
## Innehåll

- [1. Utrustningen](#1-utrustningen)
- [2. FTMS för cykel](#2-ftms-för-cykel)
  - [Indoor Bike Data (0x2AD2), notify](#indoor-bike-data-0x2ad2-notify)
  - [Control Point (0x2AD9), write med indikation](#control-point-0x2ad9-write-med-indikation)
  - [Skillnader mot löpbandsdelen](#skillnader-mot-löpbandsdelen)
  - [Cycling Power Service (0x1818) som reserv](#cycling-power-service-0x1818-som-reserv)
- [3. Concept2 PM5 över Bluetooth](#3-concept2-pm5-över-bluetooth)
- [4. QZ-vägen](#4-qz-vägen)
- [5. Förslag på klasser och gränssnitt](#5-förslag-på-klasser-och-gränssnitt)
- [6. Hårdvaruprov som kommer att behövas](#6-hårdvaruprov-som-kommer-att-behövas)
- [7. Besluten och frågorna som är kvar](#7-besluten-och-frågorna-som-är-kvar)
<!-- /toc -->

## 1. Utrustningen

Programmet byggs och provas mot den här utrustningen. Andra enheter med samma protokoll bör fungera, men är inte provade.

| Utrustning | Modell | Ansluten i dag |
|---|---|---|
| Löpband | Gymkompaniet Recoil, FTMS, 1–18 km/h, 0–12 % | QZ på gymdatorn håller bandet och sätter fart och lutning efter `.zwo`-passen; Zwift får farten från QZ. En anslutning åt gången |
| Pulsband | Garmin HRM-Pro+ | Fotpodden är reserv för farten i Zwift |
| Cykeltrainer | **Tacx NEO 2T Smart**. Den har Bluetooth med FTMS och ANT+ FE-C *(belagt i andrahand: recensioner, DC Rainmaker)*. **Bluetooth tar två samtidiga anslutningar, ANT+ obegränsat** (DC Rainmaker 2019), alltså inte tre. Blandar man Bluetooth och ANT+ rapporterar användare att styrningen ibland tappas, så det beror på app och ordning. Set Target Power över FTMS är inte belagt ur Tacx egen dokumentation | Cykelpassen körs i Zwift på gymdatorn. Hur trainern är ansluten till Zwift (Bluetooth, ANT+, genom QZ) är inte kontrollerat |
| SkiErg | Concept2 SkiErg med **PM5** | Passen körs i ErgZone-appen, som alltså är ansluten till monitorn under passet. Resultaten går till Concept2:s loggbok och Strava |
| Gymdatorn | Windows | Kör Zwift, QZ och Lopanalys |

**Okänt än:** om Zwift styr motståndet på trainern i dag, PM5:ns firmware, och om SkiErgen står inom Bluetooth-räckhåll från gymdatorn.

## 2. FTMS för cykel

*Avsnittet är ej verifierat mot källa (ur minnet av FTMS 1.0), utom det här som läsaren bekräftade: bit 0 i Indoor Bike Data är omvänd, Set Target Power (0x05) är sint16 i 1 W, Set Target Resistance Level (0x04) är uint8 med 0,1, simuleringen (0x11) bär vind, lutning, rullmotstånd och luftmotstånd, Supported Power Range (0x2AD8) är sint16, sint16, uint16, och svaret `0x80 0x05 0x05` betyder *control not permitted*.*

Samma tjänst som löpbandet (0x1826), med en annan datakaraktäristik och andra målvärden.

### Indoor Bike Data (0x2AD2), notify

Två byte flaggor, sedan fälten i den här ordningen. Allt är little-endian.

| Bit | Fält | Typ | Upplösning |
|---|---|---|---|
| 0 | *More Data*. **0** betyder att momentan fart finns (omvänd logik, som i Treadmill Data) | uint16 | 0,01 km/h |
| 1 | Medelfart | uint16 | 0,01 km/h |
| 2 | Momentan kadens | uint16 | 0,5 varv/min |
| 3 | Medelkadens | uint16 | 0,5 varv/min |
| 4 | Total sträcka | uint24 | 1 m |
| 5 | Motståndsnivå | sint16 | utan enhet |
| 6 | **Momentan effekt** | sint16 | 1 W |
| 7 | Medeleffekt | sint16 | 1 W |
| 8 | Energi: totalt, per timme, per minut | uint16, uint16, uint8 | kcal |
| 9 | Puls | uint8 | slag/min |
| 10 | MET | uint8 | 0,1 |
| 11 | Förfluten tid | uint16 | s |
| 12 | Återstående tid | uint16 | s |

Farten är trainerns låtsasfart (svänghjulet) och säger inget i ett test. Det som används är effekt och kadens. Trainrar delar ibland upp fälten på flera paket med *More Data* satt, så tolken ska slå ihop det senaste av varje fält.

### Control Point (0x2AD9), write med indikation

| Opkod | Namn | Parameter |
|---|---|---|
| 0x00 | Request Control | ingen |
| 0x01 | Reset | ingen |
| 0x04 | Set Target Resistance Level | uint8, 0,1 (utan enhet) |
| **0x05** | **Set Target Power (ERG)** | sint16, 1 W |
| 0x07 | Start or Resume | ingen |
| 0x08 | Stop or Pause | uint8: 1 = stopp, 2 = paus |
| **0x11** | Set Indoor Bike Simulation Parameters | vind sint16 (0,001 m/s), lutning sint16 (0,01 %), rullmotstånd uint8 (0,0001), luftmotstånd uint8 (0,01 kg/m) |
| 0x12 | Set Wheel Circumference | uint16, 0,1 mm |
| 0x13 | Spin Down Control | uint8: 1 = starta, 2 = hoppa över |
| 0x14 | Set Targeted Cadence | uint16, 0,5 varv/min |
| 0x80 | Svaret | begärd opkod, resultatkod (samma koder som i `ftms.py`) |

- **Supported Power Range (0x2AD8):** sint16 min, sint16 max, uint16 steg, i watt. Används som fartintervallet används i dag: varje mål kläms till intervallet.
- **Supported Resistance Level Range (0x2AD6):** sint16 min, sint16 max, uint16 steg, 0,1.
- **Fitness Machine Feature (0x2ACC):** fyra byte maskinens funktioner och fyra byte målvärden. Biten för *Power Target Setting* (bit 3 i den andra halvan) säger om ERG finns, och *Indoor Bike Simulation* (bit 13) om simulering finns. Läs den före första kommandot.
- **Fitness Machine Status (0x2ADA):** 0x08 = måleffekten ändrad (sint16), 0x07 = motståndet ändrat, 0x12 = simuleringsparametrarna ändrade, 0xFF = kontrollen förlorad. 0x01–0x04 är samma som för bandet.

### Skillnader mot löpbandsdelen

| | Löpband (`ftms.py` i dag) | Cykel |
|---|---|---|
| Data | Treadmill Data 0x2ACD | Indoor Bike Data 0x2AD2 |
| Målvärde | fart 0x02 och lutning 0x03 | effekt 0x05 (test), motstånd 0x04 eller simulering 0x11 (fri körning) |
| Intervall | 0x2AD4 och 0x2AD5 | 0x2AD8 och 0x2AD6 |
| Start | START på panelen, säkerhetsnyckel | inget: cyklisten trampar. 0x07 skickas ändå efter Request Control, vissa trainrar kräver det |
| Stopp | bandet stannar | effektmålet släpps eller sätts lågt; hjulet rullar ut |
| "Går" | fart över 0,05 km/h | kadens över 0, eller effekt över 0 |
| Håller länken vid liv | läser var 45:e sekund när bandet står | behövs troligen inte, provas |
| Fällan | en anslutning åt gången, QZ håller bandet | samma sak med Zwift eller QZ; och ERG "låser" vid låg kadens (motståndet ökar när kadensen faller), så testet behöver en regel för kadens under cirka 50 |

**Kan delas rakt av:** `_Reader`, `Range` och `_snap`, hela Control Point-maskineriet (`_command`, `_write_and_wait`, `_request_control`, `parse_control_response`, resultatkoderna, omförsöket vid *control not permitted*), statushanteringen för 0x01–0x04 och 0xFF, `SupervisedConnection` med återanslutning och att målen skickas om efter en återanslutning.

**Sökningen måste skilja maskinerna åt.** `DeviceManager.scan_treadmills()` tar i dag allt som annonserar 0x1826, så en trainer skulle hamna i listan över löpband. FTMS-annonsen har tjänstedata med maskintypen (bit 0 löpband, bit 4 roddmaskin, bit 5 cykel). Finns inte tjänstedatan avgör karaktäristikerna efter anslutning (0x2ACD eller 0x2AD2).

### Cycling Power Service (0x1818) som reserv

För trainrar utan FTMS, eller en effektmätare på en vanlig cykel. Bara läsning, alltså "manuellt läge med mätdata".

- **Cycling Power Measurement (0x2A63), notify:** flaggor uint16, sedan **momentan effekt sint16 (W)**, som alltid finns. Därefter efter flaggorna: bit 0 balans (uint8, 0,5 %), bit 2 ackumulerat vridmoment (uint16), bit 4 hjulvarv (uint32 + tid uint16 i 1/2048 s), **bit 5 vevvarv** (uint16 antal + uint16 tid i 1/1024 s).
- **Kadensen** räknas ur två paket: skillnaden i vevvarv delat med skillnaden i tid, gånger 60. Båda räknarna slår runt vid 65536, och samma tid två gånger betyder "inget nytt varv", inte kadens 0 direkt.
- Cycling Speed and Cadence (0x1816) är en tredje väg för kadens. Den behövs knappast.

## 3. Concept2 PM5 över Bluetooth

PM5 har en egen tjänstefamilj på basen `CE06xxxx-43E5-11E4-916C-0800200C9A66`. **UUID:erna nedan är belagda mot `lib/pm5-ble.js` i ergarcade/pm5-base:** tjänsterna 0x0010 (information), 0x0020 (control: 0x0021 skriv, 0x0022 svar) och 0x0030 (rowing), och karaktäristikerna 0x0031–0x0039, 0x003A–0x003C, 0x003D (force curve) och 0x0080 (multiplexed). Rad 0x0034 heter *generalStatusRate* där. pm5-base noterar att 0x003D inte gick att läsa i rowing-tjänsten i praktiken (NotFoundError), så kraftkurvan är fortfarande ej provad. Fältens ordning och byteantal är inte kontrollerade. Läsaren 2026-11-15 gav samma UUID:er ur Concept2:s PDF och github.com/skyronic/pm5-power (rowing CE060030, General Status CE060031, Additional Status 1 CE060032, 2 CE060033, Additional Stroke Data CE060036), men kraftkurvans UUID stod inte i träffarna; PDF:en gav bara bolagstext. Tacx NEO 2T: ingen träff bekräftar Set Target Power över FTMS, och två samtidiga Bluetooth-anslutningar är fortfarande bara antydda (DC Rainmaker 2019).

| Tjänst | UUID (xxxx) | Innehåll |
|---|---|---|
| Information | 0x0010 | modell, serienummer, firmware, maskintyp (rodd, skid, cykel) *ej verifierat mot källa* |
| Control | 0x0020 *(läsaren)* | 0x0021 skriv CSAFE-ramar till PM5, 0x0022 svaren *ej verifierat mot källa* |
| Rowing | 0x0030 *(läsaren)* | mätdatan nedan; samma tjänst för SkiErg |

| Karaktäristik | Namn | Det programmet behöver |
|---|---|---|
| 0x0031 | General Status, 19 byte *(läsaren)* | förfluten tid, sträcka, passets typ och tillstånd, tillstånd för rodd och drag, drag factor *(läsaren)*. Tid 3 byte i 0,01 s, sträcka 3 byte i 0,1 m *ej verifierat* |
| 0x0032 | Additional Status 1 | fart, **takt (drag/min)**, puls, **aktuellt tempo/500 m**, medeltempo, vilosträcka och vilotid; tempot i 0,01 s *(läsaren)* |
| 0x0033 | Additional Status 2 | **medeleffekt (W)**, kalorier, delsträckans medelvärden *(läsaren)*; intervallnummer och fältordningen *ej verifierat* |
| 0x0034 | Sample Rate | hur ofta 0x0031–0x0033 skickas *(läsaren)*. 0 = 1 s, 1 = 500 ms (förval), 2 = 250 ms, 3 = 100 ms *ej verifierat; läsaren bekräftade bara att 1 = 500 ms* |
| 0x0035 | Stroke Data, 20 byte | **ett paket per drag**: draglängd (0,01 m), dragtid (0,01 s), återgångstid, sträcka per drag, toppkraft, medelkraft, arbete per drag, dragnummer. Skickas två gånger per drag, vid dragets slut och vid återgångens slut *(läsaren; fältordningen ej verifierad)* |
| 0x0036 | Additional Stroke Data | **effekt i draget (W)**, kalorier per timme, dragnummer; skickas efter varje drag *(läsaren; fältordningen ej verifierad)* |
| 0x0037–0x003A | Delsträckor och sammanfattning | passets facit efteråt; 0x0039 är *End of Workout Summary* *(läsaren)*, resten *ej verifierat* |
| 0x003D | Force Curve | **kraftkurvan**: flera paket per drag, huvud med antal paket och antal värden, sedan kraftvärden som 16-bitars tal *ej verifierat; kraftkurvan finns inte i äldre PM5-firmware, och en firmware från 2026 har ett nytt format (läsaren), så PM5:s firmwareversion ska läsas vid anslutning och formatet läsas ur specifikationen* |
| 0x0080 | Multiplexed | alla ovan i en ström, första byten säger vilken *ej verifierat* |

**Det som ger siffrorna:**
- **Watt:** 0x0036 per drag och 0x0033 som medel. Concept2 räknar effekten ur tempot: watt = 2,80 / (sekunder per meter)³. Tempot 2:00/500 m är alltså 203 W.
- **Tempo/500 m:** 0x0032.
- **Takt:** 0x0032, och exakt ur tiderna i 0x0035.
- **Drag för drag:** 0x0035 ger dragtid och återgångstid, alltså rytmen mellan drag och återgång som staktekniken vill ha. Tidsstämpeln per drag gör att kamerans bilder kan paras med dragen.
- **Kraftkurva:** 0x003D.
- **Fällan:** en notifiering kan ibland vara 1 byte i stället för 19 *(läsaren, forumfynd)*. Tolken ska behålla det som gick att läsa, som `parse_treadmill_data` gör med `truncated`.

**PM5 går inte att styra.** Motståndet är luft: spjället ställs för hand och åkaren bestämmer effekten med hur hårt hen drar. Det finns inget ERG-läge. SkiErg blir därför alltid **manuellt läge med mätdata**: programmet visar "Håll 1:55/500 m" (eller watt), och visar bredvid vad åkaren faktiskt håller.

**CSAFE** är Concept2:s kommandoprotokoll och går över Control-tjänsten. Med det kan ett pass ställas upp på monitorn (typ, sträcka eller tid, vila, delsträckor) och monitorns skärm bytas. Det ändrar aldrig motståndet. Mätdatan kommer så fort någon drar, även utan pass (*Just Row*, som heter *Just Ski* på SkiErg).

**Byggt (monitorn tar emot träningspass från en telefon, så programmet kan lägga in testet på samma sätt):** testet läggs in som ett pass med variabla intervall, ett intervall per steg med längd, vila och måltempo (`devices/csafe.py`, `Pm5Ergometer.program_workout`). Ramarna skrivs till 0x0021 i bitar om 20 byte, och svaret läses på 0x0022. Tar monitorn inte emot en ram avbryts programmeringen, operatörssidan säger det, och testet går vidare med bara läsning.

| Del av CSAFE | Läge |
|---|---|
| Startflagga 0xF1, stoppflagga 0xF2, byte-stuffing (0xF3 före byten, byten ersatt med `byte & 0x03`, för 0xF0–0xF3) | *Belagt 2026-11-14* mot droogmic/Py3Row (`pyrow/csafe/csafe_cmd.py` och `csafe_dic.py`), genom läsaren |
| Checksumman: XOR av ramens innehåll före stuffing, utan flaggorna | *Belagt* mot samma kod |
| Ett kommando som (id, antal byte, data), och omslaget 0x1A runt PM-egna kommandon | *Belagt* mot samma kod |
| `CSAFE_PM_SET_SPLITDURATION` = 0x05 med en typbyte och fyra byte | *Belagt* mot samma kod (används inte i passet, men visar att tabellen stämmer där den går att jämföra) |
| Övriga PM-egna kommandon: 0x01 passtyp, 0x03 längd, 0x04 vila, 0x06 måltempo, 0x13 skärm, 0x14 konfigurera, 0x17 intervalltyp, 0x18 intervallnummer | *Ur minnet* av Concept2:s *PM CSAFE Communication Definition*: fanns inte i den lästa koden |
| Passtyp 8 (variabla intervall), intervalltyp 0 (tid), enheterna (0,01 s för längd och tempo, hela sekunder för vila), kommandonas ordning och skärmläget som visar passet | *Ur minnet* |
| Byteordningen i flerbytevärden: mest signifikanta byten först (`PM_BYTE_ORDER`) | *Ur minnet*. Py3Row skickar minst signifikanta först, så det här är det första att vända om monitorn visar fel tal |
| Vila 0 s i ett intervall, antalet intervall (programmet skickar högst 30) och en ram uppdelad på flera skrivningar | *Ej belagt* |

**FTMS på PM5:** nyare PM5 har FTMS för RowErg och BikeErg, men SkiErg gick inte att bekräfta hos Concept2 *(läsaren, osäkert; en tredjepartsguide, abscreations.netlify.app, uppger 2026-11-15 att RowErg, BikeErg och SkiErg med PM5 stöder FTMS över Bluetooth och att PM4 och äldre inte gör det; krav på firmware över 150 (pelobuddy.com) och PM5-firmware 22.015/156/722 eller senare (Garmin-forum) är obekräftat mot Concept2; kontrollerat igen: ingen officiell källa säger att PM5 på SkiErg annonserar FTMS, och firmware-anteckningarna hittades inte)*. FTMS har ingen datatyp för skidmaskin och ingen kraftkurva. Programmet ska använda Concept2:s egen tjänst.

**Flera anslutningar samtidigt:** troligen **nej**. Enligt Concept2:s forum och en apptillverkare har PM5 en enda anslutning för appar, och en andra app hittar inte monitorn medan ErgData är ansluten *(läsaren; inget officiellt dokument hittades)*. Har ErgZone monitorn kan Lopanalys alltså inte ansluta, på samma sätt som QZ och bandet. (användare skriver samma sak om FTMS-anslutningen, en app åt gången, medan en pulsgivare går över en egen länk och ANT+ kan användas parallellt. Anekdotiskt, Concept2 säger inget om gränsen.) Pulsbandet påverkas inte: PM5 ansluter själv till bandet, och det är en annan roll *(läsaren)*. HRM-Pro+ tar flera Bluetooth-anslutningar *ej verifierat*, så programmet kan läsa pulsen direkt från bandet som i dag.

Bilden är inte entydig: en annan sökning fann användare som kör ErgData tillsammans med en Garmin-klocka, och ett forumsinlägg som kallar gränsen ett designval snarare än en hårdvarugräns *(läsaren)*. Det avgörs med provet i avsnitt 6. Går det inte finns två andra vägar för den som vill ha ErgZone igång:
- **ANT+ parallellt med Bluetooth** fungerar på PM5 *(läsaren, med blandade rapporter om stabiliteten)*. Det kräver en ANT+-sticka i gymdatorn och en egen enhetsmodul, och föreslås inte nu.
- **Resultatet efteråt ur ErgZone.** ErgZones koppling ger passets resultat och kraftkurva som CSV. Det ger ingen livevisning och binder programmet till ett ErgZone-konto, så det duger inte för en produkt.

## 4. QZ-vägen

**Känt** (ur `qz.py`): QZ skickar `{"msg": "workout", "content": {…}}` en gång i sekunden till alla klienter. Innehållet har bland annat `deviceType` (1 = löpband), `speed`, `inclination`, `heart`, `distance` och **`cadence`**. Kommandona är `setspeed` (bara löpband), `setresistance` (på ett löpband blir det lutningen), `start`, `pause` och `stop`. Porten läses ur registret.

**Okänt:**
- om `content` har ett fält för effekt när en cykel är ansluten. `qz.py` listar fälten "among many others" och läser inget wattfält,
- vilket värde `deviceType` har för en cykel. `QzTreadmill` avvisar i dag allt som inte är 1,
- om det finns ett kommando för måleffekt. `setresistance` sätter på en cykel rimligen motståndsnivån, inte watt, och det duger inte för ett laktattest,
- om cykeln alls körs genom QZ i gymmet. QZ är bara provat mellan bandet och Zwift.

**Slutsats:** FTMS direkt är huvudvägen för cykel. En `QzBike` skrivs bara om det behövs att kunna testa med QZ igång. Då börjar det med att lyssna (utan att skicka något) på `workout`-meddelandet med trainern ansluten i QZ, och läsa fälten ur det. QZ:s inställningar rörs inte.

## 5. Förslag på klasser och gränssnitt

**Så blev det byggt:** `Ergometer` har de två egenskaperna nedan. Det finns ingen `Target`-klass: `set_target(load, incline)` tar belastningen i sportens enhet, och ett steg utan mål (`Stage.free`) sätts med `set_free()`. `ErgometerState` ärver `TreadmillState` och bär watten i `speed_kmh`, så att körningen läser samma fält för alla sporter. `FtmsBike` ärver `FtmsTreadmill` i stället för en gemensam `ftms_common.py`, eftersom `ftms.py` inte rörs före bandets prov. `cycling_power.py` och kraftkurvan är inte byggda.

**Två egenskaper i stället för en.** I dag betyder `controllable = False` både "operatören ställer in" och "värdena är mål, inte mätningar" (`measured=False`). SkiErg med PM5 är ett tredje fall: går inte att styra, men mäter. Körningen (`assessments/runner.py`) ska därför fråga efter två saker:

| | `controllable` | `measured` | Körningen gör |
|---|---|---|---|
| FTMS-band, FTMS-trainer i ERG, QZ | ja | ja | sätter målet själv |
| SkiErg med PM5, cykel med bara effektmätare | nej | ja | visar målet, visar utfallet bredvid, och märker steget om utfallet låg utanför ett band (till exempel ±5 %) |
| Manuellt band, manuell cykel, SkiErg utan Bluetooth | nej | nej | visar målet och väntar på operatörens bekräftelse; för SkiErg och cykel skriver operatören in medelvärdet från monitorn efter steget |

**Gränssnittet** (`devices/base.py`):
- `Ergometer` (Protocol): `sport` (`run`, `bike`, `ski`), `controllable`, `target_kind` (`speed`, `power` eller `pace`), `limits`, `scan`, `connect`, `disconnect`, `set_target(Target)`, `start`, `stop`, `state`.
- `Target`: ett av `speed_kmh` + `incline_pct`, `power_w`, eller `pace_s_per_500m`. Protokollens steg bär en `Target` i stället för fart och lutning.
- `ErgometerState`: `t`, `power_w`, `cadence` (varv/min eller drag/min), `speed_kmh`, `pace_s_per_500m`, `incline_pct`, `distance_m`, `running`, `target`, `measured`. `TreadmillState` kan ligga kvar som den är tills löpningen är provad, och `Treadmill` blir sedan ett `Ergometer` med `target_kind = "speed"`.
- `StrokeSample` på ett eget ämne på bussen: tid, dragtid, återgångstid, draglängd, effekt, toppkraft och kraftkurvan som lista.

**Modulerna:**

| Fil | Innehåll |
|---|---|
| `ftms_common.py` | det delade ur `ftms.py`: `_Reader`, `Range`, kommandona, Control Point-maskineriet i en bas `FtmsMachine(SupervisedConnection)` |
| `ftms.py` | `FtmsTreadmill(FtmsMachine)`, oförändrat beteende |
| `ftms_bike.py` | `parse_indoor_bike_data`, `parse_power_range`, `cmd_set_target_power`, `cmd_set_simulation`, `FtmsBike(FtmsMachine)` |
| `cycling_power.py` | `parse_power_measurement`, `CyclingPowerMeter` (bara läsning) |
| `pm5.py` | rena tolkar per karaktäristik, hopsättning av kraftkurvan, `Pm5Ergometer` (`controllable = False`: belastningen läses, passet programmeras) |
| `csafe.py` | CSAFE-ramarna: flaggor, stuffing, checksumma och passet med variabla intervall |
| `manual.py` | `ManualErgometer` för alla sporter; `ManualTreadmill` blir ett specialfall |
| `manager.py` | `connect_ergometer(sport, mode)`, sökning per maskintyp, minnet per sport, och samma vägran som för QZ när en annan app håller enheten |

**Simulatorerna** (`sim.py`), så att hela testerna går utan hårdvara:
- `SimBike`: i ERG följer effekten målet med ett par sekunders eftersläpning och lite brus, kadensen ligger runt 85, och under kadens 50 faller effekten (ERG-låsningen). Utan ERG ges effekten av kadens och motstånd.
- `SimSkiErg`: ger drag i en takt (30–50 drag/min), effekt per drag med brus, tempo ur effekten med Concept2:s formel och en klockformad kraftkurva. En "åkare" följer det visade målet med en liten avvikelse, så bandet på ±5 % går att prova.
- `SimHeartRate`: kravet räknas i dag ur fart och lutning. Det behöver en väg för effekt (watt per kilo) och en lägre maxpuls per sport.
- Låtsade Bluetooth-klienter som i dagens tester (`client_cls` skickas in): en FTMS-cykel som svarar på 0x05 och en PM5 som skickar 0x0031–0x0036 och 0x003D, även stympade paket.

## 6. Hårdvaruprov som kommer att behövas

Proven står nu i [PROV.md](../PROV.md#cykeltrainern), där läget skrivs. Tabellen här är underlaget de skrevs ur.

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Trainern hittas | Stäng Zwift och QZ, sök från gymdatorn | Trainern syns som cykel, inte i listan över löpband | |
| Trainerns funktioner | Anslut och läs 0x2ACC och 0x2AD8 | ERG finns, effektintervallet visas (till exempel 0–2000 W) | |
| Mätdata cykel | Trampa 2 min | Effekt och kadens stämmer med Zwift eller Garmin inom ±2 % respektive ±2 varv/min | |
| ERG | Sätt 150 → 200 → 250 W vid kadens 85 | Trainern når målet inom cirka 5 s och håller det inom ±5 W i medel över 30 s | |
| ERG vid låg kadens | Sänk kadensen under 50 mitt i ett steg | Programmet varnar; steget märks; ingen krasch | |
| Stopp cykel | Avsluta testet | Motståndet släpper, trainern går att trampa lätt | |
| En anslutning, cykel | Försök ansluta med Zwift igång | Tydligt fel på svenska om att stänga Zwift, eller så fungerar båda (skriv vilket) | |
| Återanslutning cykel | Dra trainerns sladd under ett steg, sätt i den | Programmet ansluter igen och skickar måleffekten på nytt | |
| Cycling Power | Om trainern saknar FTMS, eller med en effektmätare | Effekt och kadens visas, läget blir "mätdata utan styrning" | |
| PM5 hittas | Väck monitorn, sök från gymdatorn | PM5 syns med serienummer; räckvidden från gymdatorn räcker | |
| PM5 mätdata | Staka 2 min *Just Ski* | Tempo/500 m, watt och takt stämmer med monitorns skärm | |
| PM5 drag för drag | Staka 20 drag och räkna | 20 drag i programmet, dragtid och återgångstid rimliga (cirka 0,6 s och 0,9 s) | |
| PM5 kraftkurva | Jämför med kraftkurvan på monitorn | Samma form, ingen kurva tappas vid takt 50 | |
| PM5 och ErgZone samtidigt | Anslut ErgZone på telefonen först, sedan programmet, och tvärtom | Skriv vad som händer; går det inte ska programmet säga "Stäng ErgZone först" | |
| PM5 stympade paket | Kör 20 min och läs loggen | Inga fel av korta notifieringar | |
| Pulsen på SkiErg | HRM-Pro+ till både PM5 och programmet | Pulsen syns på båda | |
| QZ med cykel (bara om QZ-vägen ska finnas) | Lyssna på QZ:s websocket med trainern ansluten i QZ | Fälten för effekt och kadens och värdet på `deviceType` är nedskrivna | |
| Laktattrappa cykel | Hela testet med ERG och pulsband | Effekten byts vid varje steg, pausen för provet fungerar, medeleffekten per steg stämmer | |
| Trappa SkiErg | Hela testet i manuellt läge med mätdata | TV:n visar målet och utfallet, stegen utanför bandet märks | |

## 7. Besluten och frågorna som är kvar

**Beslutat 2026-11-14:**

- Trainern är en **Tacx NEO 2T**, och SkiErgen har en **PM5**.
- **Zwift, QZ och ErgZone stängs under ett test,** så att programmet ensamt håller trainern respektive PM5. Ingen QZ-väg byggs för cykel.

**Kvar:**

1. Styr Zwift motståndet på trainern i dag, och står SkiErgen i samma rum som gymdatorn?
2. Avgjort 2026-11-14: testet läggs in som ett pass på monitorn. *Just Ski* är reserven när det inte går.
3. Ska cykeltesterna också gå att köra på en trainer eller cykel som bara mäter effekt (ingen styrning), för coacher utan smart trainer?
