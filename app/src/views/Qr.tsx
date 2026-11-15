import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../i18n";

/**
 * Reads a QR code with the camera, on the device: the browser's BarcodeDetector where there is one,
 * otherwise jsQR (bundled, so it works offline). The frames never leave the device.
 */
export function Qr({ onText, onClose }: { onText: (s: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let stop = false;
    let timer = 0;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
        const v = video.current!;
        v.srcObject = stream;
        await v.play();
        const Detector = (globalThis as { BarcodeDetector?: new (o: object) => { detect(s: CanvasImageSource): Promise<{ rawValue: string }[]> } }).BarcodeDetector;
        const detector = Detector ? new Detector({ formats: ["qr_code"] }) : null;
        const jsQR = detector ? null : (await import("jsqr")).default;
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
        const tick = async () => {
          if (stop) return;
          if (v.videoWidth) {
            let found: string | null = null;
            if (detector) {
              const r = await detector.detect(v).catch(() => []);
              found = r[0]?.rawValue ?? null;
            } else if (jsQR) {
              canvas.width = v.videoWidth;
              canvas.height = v.videoHeight;
              ctx.drawImage(v, 0, 0);
              const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
              found = jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" })?.data ?? null;
            }
            if (found) {
              onText(found);
              return;
            }
          }
          timer = window.setTimeout(tick, 200);
        };
        tick();
      } catch {
        setFailed(true);
      }
    })();
    return () => {
      stop = true;
      clearTimeout(timer);
      stream?.getTracks().forEach((tr) => tr.stop());
    };
  }, []);

  return (
    <div class="qr" role="dialog" aria-label={t("ui.qr.title")}>
      <p>{failed ? t("ui.qr.no_camera") : t("ui.qr.title")}</p>
      {failed ? null : <video ref={video} playsInline muted />}
      <button class="secondary wide" onClick={onClose}>{t("ui.qr.cancel")}</button>
    </div>
  );
}
