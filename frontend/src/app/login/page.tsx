"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useApp } from "@/lib/providers";

export default function LoginPage() {
  const { t, login, token, ready, locale, setLocale } = useApp();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && token) router.replace("/");
  }, [ready, token, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : t.login.error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <form onSubmit={submit} className="card p-6 w-full max-w-sm space-y-4">
        <div>
          <h1 className="text-lg font-semibold">{t.appName}</h1>
          <p className="text-xs text-[var(--text-muted)]">{t.tagline}</p>
        </div>
        <label className="block text-sm">
          {t.login.email}
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="field mt-1 w-full"
          />
        </label>
        <label className="block text-sm">
          {t.login.password}
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="field mt-1 w-full"
          />
        </label>
        {error && (
          <p className="text-sm" style={{ color: "var(--status-critical)" }}>
            ■ {error}
          </p>
        )}
        <button type="submit" disabled={busy} className="btn w-full text-sm">
          {t.login.submit}
        </button>
        <button
          type="button"
          onClick={() => setLocale(locale === "en" ? "ar" : "en")}
          className="w-full text-xs text-[var(--text-muted)]"
        >
          {locale === "en" ? "العربية" : "English"}
        </button>
      </form>
    </div>
  );
}
