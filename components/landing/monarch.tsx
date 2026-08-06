import Image from 'next/image'

import { BOT_URL, PRICES } from '@/lib/site'

const PERKS = [
  `${PRICES.premiumReports} отчёта ИИ в день вместо ${PRICES.freeReports}`,
  `${PRICES.premiumCustomQuests} личных квестов вместо ${PRICES.freeCustomQuests}`,
  'Золотая тема карточки игрока и статус «Восходящий»',
  'Расширенные лимиты без сюрприз-пейволлов посреди дня',
]

export function Monarch() {
  return (
    <section className="border-t border-border px-6 py-20 md:px-10">
      <div className="mx-auto max-w-6xl">
        <p className="mb-3 font-mono text-xs tracking-widest text-primary uppercase">{'// Статус восходящего'}</p>
        <h2 className="mb-4 font-mono text-3xl font-bold text-balance text-foreground uppercase md:text-4xl">Хочешь больше — стань Восходящим</h2>
        <p className="mb-12 max-w-xl leading-relaxed text-pretty text-muted-foreground">ARISE бесплатна и полностью играбельна без доплат. «Восходящий» — это ускорение для тех, кому мало: больше отчётов, больше квестов и страховка от срывов серии.</p>
        <div className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:items-start">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-lg border border-primary/40 bg-card p-6 sm:col-span-2">
              <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2"><span className="font-mono text-xs tracking-widest text-primary uppercase">Восходящий</span><span className="font-mono text-2xl font-bold text-foreground">{PRICES.premium} ⭐<span className="ml-1 text-sm font-normal text-muted-foreground">/ {PRICES.premiumDays} дней</span></span></div>
              <ul className="mt-4 flex flex-col gap-2 text-sm text-muted-foreground">{PERKS.map((perk) => <li key={perk} className="flex gap-2"><span className="text-primary" aria-hidden="true">✓</span>{perk}</li>)}</ul>
            </div>
            <div className="rounded-lg border border-border bg-card p-6"><div className="mb-1 flex items-baseline justify-between gap-2"><span className="font-mono text-xs tracking-widest text-muted-foreground uppercase">Воскрешение</span><span className="font-mono text-xl font-bold text-foreground">{PRICES.revive} ⭐</span></div><p className="text-sm leading-relaxed text-muted-foreground">Отменяет потерю уровня, пока ты «при смерти» — окно спасения открыто до следующего рассвета ARISE.</p></div>
            <div className="rounded-lg border border-border bg-card p-6"><div className="mb-1 flex items-baseline justify-between gap-2"><span className="font-mono text-xs tracking-widest text-muted-foreground uppercase">Заморозка</span><span className="font-mono text-xl font-bold text-foreground">{PRICES.freeze} ⭐</span></div><p className="text-sm leading-relaxed text-muted-foreground">Спасает серию от срыва в день, когда квесты закрыть не вышло — без урона по HP.</p></div>
          </div>
          <div className="flex justify-center lg:justify-end"><div className="relative w-full max-w-[220px] sm:max-w-xs"><div aria-hidden="true" className="absolute -inset-6 -z-10 rounded-[2rem] bg-primary/10 blur-3xl" /><Image src="/hunter-card-monarch.png" alt="Карточка игрока со статусом «Восходящий»" width={800} height={1240} className="w-full rounded-lg border border-primary/30 shadow-[0_0_60px_-15px_oklch(0.82_0.13_215/0.3)]" /></div></div>
        </div>
        <p className="mt-8 font-mono text-xs tracking-widest text-muted-foreground uppercase">Оплата — Telegram Stars, прямо внутри бота. <a href={BOT_URL} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Открыть /premium</a></p>
      </div>
    </section>
  )
}
