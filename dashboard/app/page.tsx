"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, checkSession } from "@/lib/api";
import LoginGate from "@/components/LoginGate";
import Progress from "@/components/Progress";
import Shell from "@/components/Shell";
import { ErrorNote, Logo, Skeleton } from "@/components/ui";

type State = "checking" | "out" | "in" | "unreachable";

export default function Page() {
  const [state, setState] = useState<State>("checking");
  const [reason, setReason] = useState("");

  const probe = useCallback(async () => {
    setState("checking");
    try {
      const { authenticated } = await checkSession();
      setState(authenticated ? "in" : "out");
    } catch (error) {
      if (error instanceof ApiError && error.code === "unreachable") {
        setReason(error.message);
        setState("unreachable");
        return;
      }
      setState("out");
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  if (state === "checking") {
    return (
      <>
        <Progress />
        <div className="grid min-h-dvh place-items-center p-5">
          <Skeleton className="h-40 w-[min(24rem,100%)] rounded-xl" />
        </div>
      </>
    );
  }

  if (state === "unreachable") {
    return (
      <div className="grid min-h-dvh place-items-center p-5">
        <div className="flex w-[min(26rem,100%)] flex-col items-center gap-3.5 text-center">
          <Logo size={34} />
          <strong className="text-lg">Hydra DNS</strong>
          <ErrorNote error={reason} onRetry={() => void probe()} />
        </div>
      </div>
    );
  }

  if (state === "out") {
    return (
      <>
        <Progress />
        <LoginGate onSignedIn={() => setState("in")} />
      </>
    );
  }

  return (
    <>
      <Progress />
      <Shell onSignedOut={() => setState("out")} />
    </>
  );
}
