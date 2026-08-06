import { BOT_URL } from '@/lib/site'

export function Hero() {
  return (
    <section className="relative overflow-hidden px-6 pt-16 pb-20 md:px-10 md:pt-24 md:pb-28">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 opacity-[0.07]" style={{ backgroundImage: 'linear-gradient(oklch(0.82 0.13 215) 1px, transparent 1px), linear-gradient(90deg, oklch(0.82 0.13 215) 1px, transparent 1px)', backgroundSize: '48px 48px' }} />
      <div className="relative mx-auto flex max-w-6xl flex-col items-start gap-12 lg:flex-row lg:items-center">
        <div className="flex-1">
          <p className="mb-6 flex items-center gap-2 font-mono text-xs tracking-widest text-primary uppercase"><span aria-hidden="true">✦</span> Для тех, кто решил перестать быть E-рангом</p>
          <h1 className="mb-6 font-mono text-4xl leading-tight font-bold text-balance text-foreground uppercase md:text-6xl">Ты получил<br /><span className="text-primary">приглашение</span><br />ARISE.</h1>
          <p className="mb-10 max-w-md text-base leading-relaxed text-pretty text-muted-foreground">Telegram-бот превращает твою реальную жизнь в RPG: ежедневные квесты, опыт, уровни, HP, серии дисциплины и боссы недели. Пропустил день — система накажет.</p>
          <div className="flex flex-wrap items-center gap-4"><a href={BOT_URL} target="_blank" rel="noopener noreferrer" className="rounded-md bg-primary px-7 py-3 font-mono text-sm font-bold tracking-wider text-primary-foreground uppercase transition-opacity hover:opacity-90">Принять контракт</a><a href="#how" className="rounded-md border border-border px-7 py-3 font-mono text-sm tracking-wider text-foreground uppercase transition-colors hover:border-primary/50">Как это работает</a></div>
        </div>
        <div className="w-full max-w-sm flex-shrink-0 lg:w-96"><div className="rounded-lg border border-primary/30 bg-card shadow-[0_0_60px_-15px_oklch(0.82_0.13_215/0.3)]"><div className="flex items-center justify-between border-b border-border px-5 py-3"><span className="font-mono text-xs tracking-widest text-primary uppercase">⚙ ARISE // СТАТУС</span><span className="sys-pulse size-2 rounded-full bg-primary" aria-hidden="true" /></div><div className="flex flex-col gap-4 px-5 py-6 font-mono text-sm"><div className="flex justify-between gap-3"><span className="text-muted-foreground">ИГРОК</span><span className="text-right text-foreground">это ты</span></div><div className="flex justify-between"><span className="text-muted-foreground">РАНГ</span><span className="font-bold text-primary">B</span></div><div className="flex justify-between"><span className="text-muted-foreground">УРОВЕНЬ</span><span className="text-foreground">27</span></div><div><div className="mb-1 flex justify-between"><span className="text-muted-foreground">HP</span><span className="text-foreground">80/100</span></div><div className="text-primary" aria-hidden="true">████████░░</div></div><div><div className="mb-1 flex justify-between"><span className="text-muted-foreground">XP</span><span className="text-foreground">340/500</span></div><div className="text-foreground/70" aria-hidden="true">███████░░░</div></div><div className="flex justify-between"><span className="text-muted-foreground">СЕРИЯ</span><span className="text-foreground">14 дней 🔥</span></div><div className="mt-2 rounded border border-primary/25 bg-primary/5 px-3 py-2 text-xs leading-relaxed text-muted-foreground">{'>'} Обнаружена аномалия. Награда повышена.<br />{'>'} Окно закроется в полночь.</div></div></div></div>
      </div>
    </section>
  )
}
