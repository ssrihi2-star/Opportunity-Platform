"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useApp } from "@/lib/providers";
import { Shell } from "@/components/ui";

export function Guard({ children }: { children: React.ReactNode }) {
  const { token, ready, t } = useApp();
  const router = useRouter();

  useEffect(() => {
    if (ready && !token) router.replace("/login");
  }, [ready, token, router]);

  if (!ready)
    return (
      <Shell>
        <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>
      </Shell>
    );
  if (!token) return null;
  return <Shell>{children}</Shell>;
}
