import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import KeyboardInset from "@/components/KeyboardInset";
import RegisterServiceWorker from "@/components/RegisterServiceWorker";
import { WindowScrollbar } from "@/components/ui";
import {
  CHROME_BLACK,
  CHROME_DARK,
  CHROME_DARK_MEDIA,
  CHROME_ID,
  CHROME_LIGHT,
} from "@/lib/chrome";
import "./globals.css";

const sans = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono-face",
});

const THEME_BOOTSTRAP = `try{var d=document.documentElement,t=localStorage.getItem("hydra-theme");if(t==="light"||t==="dark"){d.dataset.theme=t}if(localStorage.getItem("hydra-black")==="1"){d.dataset.black="true";var m=document.createElement("meta");m.id="${CHROME_ID}";m.setAttribute("name","theme-color");m.setAttribute("media","${CHROME_DARK_MEDIA}");m.setAttribute("content","${CHROME_BLACK}");document.head.insertBefore(m,document.head.firstChild)}}catch(e){}`;

export const metadata: Metadata = {
  title: "Hydra DNS",
  description: "One dashboard over several pooled AdGuard DNS accounts.",
  applicationName: "Hydra DNS",
  manifest: "/manifest.webmanifest",
  robots: { index: false, follow: false },
  icons: {
    icon: [
      { url: "/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
  appleWebApp: {
    capable: true,
    title: "Hydra",
    statusBarStyle: "black-translucent",
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: CHROME_LIGHT },
    { media: "(prefers-color-scheme: dark)", color: CHROME_DARK },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="font-sans antialiased">
        {children}
        <WindowScrollbar />
        <KeyboardInset />
        <RegisterServiceWorker />
      </body>
    </html>
  );
}
