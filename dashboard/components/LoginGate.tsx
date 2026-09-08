"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, login } from "@/lib/api";
import { Alert, Button, Input, Logo, Spinner } from "./ui";

export default function LoginGate({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  useEffect(() => {
    if (!busy) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), 3500);
    return () => clearTimeout(timer);
  }, [busy]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(password);
      onSignedIn();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not sign in. Try again.");
      setPassword("");
      input.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-dvh place-items-center p-5">
      <form
        onSubmit={submit}
        className="hy-rise flex w-[min(24rem,100%)] flex-col gap-3 rounded-xl border border-line bg-surface p-6 shadow-md"
      >
        <div className="flex flex-col items-center gap-2 text-center">
          <Logo size={34} />
          <h1 className="m-0 text-xl font-semibold tracking-[-0.02em]">Hydra DNS</h1>
          <p className="m-0 text-xs text-muted">
            One dashboard over several AdGuard DNS accounts.
          </p>
        </div>

        <Input
          ref={input}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Dashboard password"
          autoComplete="current-password"
          aria-label="Dashboard password"
          disabled={busy}
          className="mt-2"
        />

        <Button variant="primary" size="lg" type="submit" disabled={busy || !password}>
          {busy && <Spinner />}
          {busy ? "Signing in…" : "Sign in"}
        </Button>

        {slow && busy && (
          <p className="m-0 text-center text-xs text-muted">Still waiting on the API.</p>
        )}

        {error && (
          <Alert tone="danger" role="alert">
            {error}
          </Alert>
        )}
      </form>
    </div>
  );
}
