import type { Metadata } from "next";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Context-compact",
  description: "Model-driven, recoverable context compaction for LangChain agents: playground, comparisons and results.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <Nav />
        <main className="mx-auto max-w-[1600px] px-4 py-5">{children}</main>
      </body>
    </html>
  );
}
