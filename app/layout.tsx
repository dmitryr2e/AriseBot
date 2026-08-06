import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'

import { SITE_URL } from '@/lib/site'
import './globals.css'

const inter = Inter({ subsets: ['latin', 'cyrillic'], variable: '--font-inter' })
const jetbrainsMono = JetBrains_Mono({ subsets: ['latin', 'cyrillic'], variable: '--font-jetbrains-mono' })

const TITLE = 'ARISE — Система прокачает тебя'
const DESCRIPTION = 'ARISE превращает твою реальную жизнь в RPG: ежедневные квесты, опыт, уровни, ранги, боссы недели и ИИ-оценка твоих отчётов.'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL), title: { default: TITLE, template: '%s' }, description: DESCRIPTION, applicationName: 'ARISE', generator: 'v0.app', alternates: { canonical: '/' },
  openGraph: { type: 'website', locale: 'ru_RU', url: SITE_URL, siteName: 'ARISE', title: TITLE, description: DESCRIPTION },
  twitter: { card: 'summary_large_image', title: TITLE, description: DESCRIPTION },
  icons: { icon: [{ url: '/icon-light-32x32.png', media: '(prefers-color-scheme: light)' }, { url: '/icon-dark-32x32.png', media: '(prefers-color-scheme: dark)' }, { url: '/icon.svg', type: 'image/svg+xml' }], apple: '/apple-icon.png' },
}

export const viewport: Viewport = { colorScheme: 'dark', themeColor: '#131722' }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru" className={`bg-background ${inter.variable} ${jetbrainsMono.variable}`}><body className="antialiased font-sans">{children}{process.env.NODE_ENV === 'production' && <Analytics />}</body></html>
}
