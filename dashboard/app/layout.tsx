import type { Metadata } from "next";
import { Azeret_Mono, Bricolage_Grotesque, Inter_Tight } from "next/font/google";
import "./globals.css";

// Display: a variable grotesque with actual opinions in its letterforms, used only at
// headline sizes so its character reads without shouting.
const display = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-bricolage", display: "swap" });

// Body: Inter Tight rather than Inter — narrower, so a dense chart fits more per row
// without dropping below a readable size.
const body = Inter_Tight({ subsets: ["latin"], variable: "--font-inter-tight", display: "swap" });

// Data: every figure, plate number and stage code. Azeret has the squared-off
// instrument feel a chart's marginalia wants, and real tabular figures.
const mono = Azeret_Mono({ subsets: ["latin"], variable: "--font-azeret", display: "swap" });

export const metadata: Metadata = {
  title: "ZoNuLy — the referral atlas",
  description:
    "Finds underrated, well-funded companies, grades what they can pay, proves they are really hiring, and names the person who could refer you.",
};

// Applied before first paint so a dark-mode reader never gets a white flash. It reads
// the stored choice, falls back to the OS, and never throws in a locked-down browser.
const THEME_BOOT = `(function(){try{var t=localStorage.getItem("zonuly-theme");
if(!t){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}
if(t==="dark")document.documentElement.classList.add("dark");}catch(e){}})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable} h-full antialiased`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
