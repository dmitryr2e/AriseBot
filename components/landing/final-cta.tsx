import Link from 'next/link'

import { BOT_URL } from '@/lib/site'

export function FinalCta() {
  return (
    <section className="border-t border-border px-6 py-24 md:px-10"><div className="mx-auto flex max-w-3xl flex-col items-center text-center"><p className="mb-4 font-mono text-xs tracking-widest text-primary uppercase">{'// Уведомление ARISE'}</p><h2 className="mb-6 font-mono text-3xl leading-tight font-bold text-balance text-foreground uppercase md:text-5xl">Ты выполнил все условия для получения квеста.</h2><p className="mb-10 max-w-lg leading-relaxed text-pretty text-muted-foreground">Отказ невозможен. Точнее, возможен — но тогда завтра ты будешь тем же, кем был вчера. Следующий уровень начинается с одного решения.</p><a href={BOT_URL} target="_blank" rel="noopener noreferrer" className="rounded-md bg-primary px-10 py-4 font-mono text-base font-bold tracking-wider text-primary-foreground uppercase transition-opacity hover:opacity-90">Принять</a></div></section>
  )
}

export function SiteFooter() {
  return (
    <footer className="border-t border-border px-6 py-8 md:px-10"><div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-6 sm:flex-row"><span className="max-w-md text-center font-mono text-xs leading-relaxed tracking-widest text-muted-foreground uppercase sm:text-left">ARISE — независимый проект в жанре LitRPG</span><nav aria-label="Правовая информация" className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2"><Link href="/privacy" className="font-mono text-xs tracking-widest text-muted-foreground uppercase hover:text-primary hover:underline">Конфиденциальность</Link><Link href="/terms" className="font-mono text-xs tracking-widest text-muted-foreground uppercase hover:text-primary hover:underline">Условия</Link><a href={BOT_URL} target="_blank" rel="noopener noreferrer" className="font-mono text-xs tracking-widest text-primary uppercase hover:underline">Telegram</a></nav></div></footer>
  )
}
