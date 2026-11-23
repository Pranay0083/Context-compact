"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, API_URL, type Health } from "@/lib/api";
import { cx, Pill } from "./ui";

const LINKS = [
  { href: "/", label: "Playground" },
  { href: "/compare", label: "Compare" },
  { href: "/results", label: "Results" },
  { href: "/golden", label: "Golden set" },
  { href: "/demo", label: "Demo" },
];

export default function Nav() {
  const path = usePathname();
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .health()
      .then((h) => alive && (setHealth(h), setDown(false)))
      .catch(() => alive && setDown(true));
    return () => {
      alive = false;
    };
  }, [path]);

  return (
    <nav className="sticky top-0 z-20 border-b border-border bg-card/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span aria-hidden className="grid h-7 w-7 place-items-center rounded-lg bg-accent text-sm text-white dark:text-zinc-950">
            ⇲
          </span>
          Context-compact
        </Link>
        <div className="flex flex-wrap gap-1">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={cx(
                  "rounded-lg px-3 py-1.5 text-sm",
                  active ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-muted hover:text-foreground",
                )}
              >
                {l.label}
              </Link>
            );
          })}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          {down && (
            <Pill tone="bad" title={API_URL}>
              API offline
            </Pill>
          )}
          {health && (
            <>
              <Pill tone={health.fake_llm ? "warn" : health.has_key ? "ok" : "bad"}>
                {health.fake_llm ? "offline demo model" : health.has_key ? "Groq key loaded" : "no API key"}
              </Pill>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
