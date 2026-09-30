"use client";

import { useEffect } from "react";

import { watchKeyboard } from "@/lib/keyboard";

export default function KeyboardInset() {
  useEffect(() => watchKeyboard(), []);
  return null;
}
