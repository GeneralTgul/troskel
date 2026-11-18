# Kom igång med appen

*För den som vill träna efter ett schema i telefonen utan coach: länken, hur appen hamnar på hemskärmen, hur schemat görs och hur appen uppdateras. Det behövs inget konto och inget test.*

<!-- toc -->
## Innehåll

- [Länken](#länken)
- [Lägg appen på hemskärmen](#lägg-appen-på-hemskärmen)
- [Gör schemat](#gör-schemat)
- [Använd schemat](#använd-schemat)
- [Uppdatera appen](#uppdatera-appen)
- [Var schemat sparas](#var-schemat-sparas)
- [Det som inte är provat](#det-som-inte-är-provat)
<!-- /toc -->

## Länken

<https://generaltgul.github.io/troskel/app/>

![QR-kod till appen](images/qr-appen.png)

Skicka länken i ett meddelande, eller visa QR-koden: telefonens kamera läser den och öppnar appen. Samma länk gäller för iPhone och Android. Koden är gjord med segno (`segno.make(länken, error="m")`, skala 10) och läst tillbaka med OpenCV.

## Lägg appen på hemskärmen

**iPhone och iPad:** öppna länken i Safari, tryck på *Dela* (rutan med pilen uppåt) och *Lägg till på hemskärmen*. Appen startar sedan från ikonen, utan adressfält.

**Android:** öppna länken i Chrome, tryck på menyn (de tre prickarna) och *Lägg till på startskärmen* eller *Installera app*. Det finns också en Android-app att installera, som dessutom kan läsa pass och sömn ur Health Connect. Hur den installeras står i [Appen utan butiken](../app.md#appen-utan-butiken).

Efter första besöket fungerar appen utan nät.

## Gör schemat

Första gången appen öppnas är den tom. Tryck på *Gör ett schema själv* och svara på frågorna:

1. **Idrotter:** löpning är förvalt. Väljer du flera får de en roll var: huvudidrott, stödjande eller underhåll. Under *Styrkepass i veckan* väljer du 0–3.
2. **Din nivå:** välj *Nybörjare*, *Motionär*, *Vältränad* eller *Hög volym*. Det är den enda frågan som måste besvaras. *Minuter i veckan nu* är hur mycket du tränat de senaste veckorna; schemat börjar nära det och ökar lugnt.
3. **Dina värden** (frivilligt): maxpuls, vilopuls, pulsen och tempot vid dina trösklar (LT1 och LT2), FTP på cykeln och CSS-tempot i simning. Med dem får passen puls, tempo eller watt. Utan dem har passen zon och ansträngning. Vad fälten betyder står i [Egna värden](../app.md#egna-värden).
4. **Dagar och tider:** slå på de dagar du kan träna, och skriv hur många minuter (20–480) och vilket klockslag. En dag som är av blir vilodag.
5. **Mål:** *Ett lopp* med namn, datum, distans och måltid, och delmål före det, eller *Ett antal veckor*.
6. **Start:** dagen då schemat börjar (det börjar på måndagen samma vecka eller nästa), och ditt namn om du vill.

Tryck på *Gör schemat*. Schemat räknas fram i telefonen och lämnar den inte.

## Använd schemat

- *I dag* visar dagens pass med tid, zon och hur det ska kännas. Bocka av det som *Gjort*, *Delvis* eller *Missat*, eller lägg till ett pass utanför schemat.
- *Veckan* visar veckans pass. Ett pass går att flytta, byta eller korta med *Ändra passet*.
- *Schemat* visar blocken, tiden per vecka och zonerna. Där finns *Ändra mina värden*, om du vill fylla i eller ändra dina värden senare.
- *Mina tider* ändrar dagar och minuter, och dagar som inte går (en resa, en sjukdag). Schemat räknas om från i morgon.
- *Lägg passen i kalendern* (under *Veckan* och *Schemat*) lägger passen i telefonens kalender.

De fyra närmaste veckorna har pass, och resten är en skiss. Appen fyller på med nya veckor av sig själv när den öppnas. Mer om allt detta står i [Appen för idrottaren](../app.md).

## Uppdatera appen

Appen på hemskärmen hämtar den nya versionen själv när den öppnas med nät. Android-appen uppdateras genom att den nya APK:n installeras ovanpå den gamla, och schemat ligger kvar.

## Var schemat sparas

Schemat, det du bockat av och dina tider sparas bara i telefonen, i webbläsarens lagring för appen. Det finns inget konto och ingen kopia på någon server. *Ta bort all data på enheten* under *Om appen* tömmer allt.

## Det som inte är provat

Vägen från länken till ett schema är provad i en ren webbläsare (Chromium i telefonstorlek, tom lagring), också utan nät efter första besöket. Hemskärmen på en riktig iPhone och Android-telefon är inte provad; proven står i [PROV.md](../../PROV.md#appen-i-telefonen).
