import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../i18n";
import { qrDetector, qrScanner, type JsQr } from "../model/qr";

/**
 * Reads a QR code with the camera, on the device: the browser's BarcodeDetector where it reads QR codes,
 * otherwise jsQR (bundled, so it works offline; model/qr.ts). The frames never leave the device.
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
        const scan = qrScanner(await qrDetector(), async () => (await import("jsqr")).default as JsQr);
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
        const frame = {
          source: v,
          pixels: () => {
            canvas.width = v.videoWidth;
            canvas.height = v.videoHeight;
            ctx.drawImage(v, 0, 0);
            return ctx.getImageData(0, 0, canvas.width, canvas.height);
          },
        };
        const tick = async () => {
          if (stop) return;
          if (v.videoWidth) {
            const found = await scan(frame).catch(() => null);
            if (stop) return;
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
