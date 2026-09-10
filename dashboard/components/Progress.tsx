"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { requestsInFlight, subscribeToRequests } from "@/lib/api";

export default function Progress() {
  const busy = useSyncExternalStore(subscribeToRequests, requestsInFlight, () => 0) > 0;
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!busy) {
      setVisible(false);
      return;
    }
    const timer = setTimeout(() => setVisible(true), 400);
    return () => clearTimeout(timer);
  }, [busy]);

  return <div className="hy-progress" data-on={visible} aria-hidden="true" />;
}
