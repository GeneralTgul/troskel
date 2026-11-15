/**
 * The plan in a link, a QR code or a file. The link is `<app>#p=<data>`, where <data> is the payload's JSON,
 * compressed with deflate in the zlib format and coded as base64url without padding
 * (`lopanalys.plan.link.encode`). The fragment never reaches a server.
 */
import { strFromU8, strToU8, unzlibSync, zlibSync } from "fflate";
import { FORMAT, KIND, type StoredPlan } from "./types";

export class PlanError extends Error {
  constructor(public readonly code: "not_a_plan" | "too_new" | "broken") {
    super(code);
  }
}

function fromBase64Url(data: string): Uint8Array {
  const b64 = data.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (data.length % 4)) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function toBase64Url(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Checks the shape of a payload just enough that the views can trust it. */
export function checkPlan(value: unknown): StoredPlan {
  const v = value as Partial<StoredPlan> | null;
  if (!v || typeof v !== "object" || v.kind !== KIND) throw new PlanError("not_a_plan");
  if (typeof v.format !== "number" || v.format > FORMAT) throw new PlanError("too_new");
  if (!v.plan || !Array.isArray(v.plan.weeks) || !v.request || !Array.isArray(v.request.availability))
    throw new PlanError("broken");
  return {
    ...v,
    athlete: v.athlete ?? "",
    targets: v.targets ?? {},
    targets_log: v.targets_log ?? [],
  } as StoredPlan;
}

export function decodeData(data: string): StoredPlan {
  let json: unknown;
  try {
    json = JSON.parse(strFromU8(unzlibSync(fromBase64Url(data))));
  } catch {
    throw new PlanError("broken");
  }
  return checkPlan(json);
}

export function encodeData(stored: StoredPlan): string {
  return toBase64Url(zlibSync(strToU8(JSON.stringify(stored)), { level: 9 }));
}

/** The `p=` data of a link or a bare fragment ("#p=..."), or null when there is none. */
export function linkData(link: string): string | null {
  const hash = link.includes("#") ? link.slice(link.indexOf("#") + 1) : link;
  for (const part of hash.split("&")) if (part.startsWith("p=")) return part.slice(2).trim();
  return null;
}

export function decodeLink(link: string): StoredPlan {
  const data = linkData(link);
  if (!data) throw new PlanError("not_a_plan");
  return decodeData(data);
}

/** The contents of a `.troskel` or `.json` file, or a text holding a link (a QR code, a pasted message). */
export function readPlanText(text: string): StoredPlan {
  const s = text.trim().replace(/^﻿/, "");
  if (s.startsWith("{")) {
    let json: unknown;
    try {
      json = JSON.parse(s);
    } catch {
      throw new PlanError("broken");
    }
    return checkPlan(json);
  }
  return decodeLink(s);
}
