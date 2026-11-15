import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { readPlanText } from "../src/model/link";
import type { StoredPlan } from "../src/model/types";

// Paths from the app's folder (vitest runs there), so that the DOM tests read the same files.
const APP = process.cwd();
export const fixture = (name: string) => readFileSync(resolve(APP, "test/fixtures", name), "utf-8");
export const demoText = () => readFileSync(resolve(APP, "public/demo.troskel"), "utf-8");
export const demoPlan = (): StoredPlan => readPlanText(demoText());
