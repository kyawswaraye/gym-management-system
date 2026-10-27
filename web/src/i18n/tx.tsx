"use client";

import type { ReactNode } from "react";

import { useTx } from "@/i18n/use-tx";
import type { Vars } from "@/i18n/translate";

export function Tx({ text, vars }: { text: string; vars?: Vars }): ReactNode {
  const tx = useTx();
  return tx(text, vars);
}
