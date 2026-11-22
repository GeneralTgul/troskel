/**
 * Reading a QR code from a camera frame: the browser's BarcodeDetector where it reads QR codes, otherwise jsQR
 * (bundled, so it works offline).
 *
 * A BarcodeDetector that exists is not always one that works: on Android it needs a module of Google Play services,
 * and without it (a tablet without Play, a web view before the module is fetched) the list of formats is empty or
 * `detect` fails every time. The scanner then never found a code and never said why. So the detector is used only
 * when it lists QR codes, and the first failed `detect` hands over to jsQR for the rest of the scan.
 */

export interface Detector {
  detect(source: CanvasImageSource): Promise<{ rawValue: string }[]>;
}
export interface DetectorCtor {
  new (options: { formats: string[] }): Detector;
  getSupportedFormats?: () => Promise<string[]>;
}
export type JsQr = (data: Uint8ClampedArray, width: number, height: number, options?: { inversionAttempts: "dontInvert" }) => { data: string } | null;

/** One camera frame: the element for the detector, and its pixels for jsQR (drawn only when asked for). */
export interface Frame {
  source: CanvasImageSource;
  pixels: () => { data: Uint8ClampedArray; width: number; height: number };
}

/** The browser's detector when it reads QR codes, otherwise null. */
export async function qrDetector(
  Ctor: DetectorCtor | undefined = (globalThis as { BarcodeDetector?: DetectorCtor }).BarcodeDetector,
): Promise<Detector | null> {
  if (!Ctor) return null;
  try {
    if (Ctor.getSupportedFormats && !(await Ctor.getSupportedFormats()).includes("qr_code")) return null;
    return new Ctor({ formats: ["qr_code"] });
  } catch {
    return null;
  }
}

/** A function that reads the code of a frame, or null when the frame has none. */
export function qrScanner(detector: Detector | null, loadJsQr: () => Promise<JsQr>): (frame: Frame) => Promise<string | null> {
  let native = detector;
  let js: JsQr | null = null;
  return async (frame) => {
    if (native) {
      try {
        return (await native.detect(frame.source))[0]?.rawValue ?? null;
      } catch {
        native = null; // it does not work on this device: jsQR from here on
      }
    }
    js ??= await loadJsQr();
    const img = frame.pixels();
    return js(img.data, img.width, img.height, { inversionAttempts: "dontInvert" })?.data ?? null;
  };
}
