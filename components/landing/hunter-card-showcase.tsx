import Image from 'next/image'

export function HunterCardShowcase() {
  return (
    <section className="border-t border-border px-6 py-20 md:px-10">
      <div className="mx-auto grid max-w-6xl items-center gap-12 lg:grid-cols-2">
        <div>
          <p className="mb-3 font-mono text-xs tracking-widest text-primary uppercase">
            {'// Карточка игрока'}
          </p>
          <h2 className="mb-6 font-mono text-3xl font-bold text-balance text-foreground uppercase md:text-4xl">
            Твой прогресс — не абстрактная цифра
          </h2>
          <p className="mb-6 max-w-md leading-relaxed text-pretty text-muted-foreground">
            Команда{' '}
            <code className="rounded bg-card px-1.5 py-0.5 font-mono text-sm text-primary">
              /card
            </code>{' '}
            рисует карточку игрока: ранг, уровень, HP, характеристики и серия — всё в одном
            изображении. Перешли друзьям или сохрани как трофей.
          </p>
          <ul className="flex flex-col gap-2 font-mono text-sm text-muted-foreground">
            <li className="flex gap-2">
              <span className="text-primary" aria-hidden="true">
                →
              </span>
              QR-код с реф-ссылкой встроен прямо в карточку
            </li>
            <li className="flex gap-2">
              <span className="text-primary" aria-hidden="true">
                →
              </span>
              Отдельная золотая тема карточки для «Восходящего»
            </li>
            <li className="flex gap-2">
              <span className="text-primary" aria-hidden="true">
                →
              </span>
              HD-версия по кнопке — для сторис и обоев
            </li>
          </ul>
        </div>
        <div className="flex justify-center">
          <div className="relative w-full max-w-xs">
            <div
              aria-hidden="true"
              className="absolute -inset-6 -z-10 rounded-[2rem] bg-primary/10 blur-3xl"
            />
            <Image
              src="/hunter-card-sample.png"
              alt="Пример карточки игрока, сгенерированной ботом: ранг, уровень, HP и характеристики"
              width={800}
              height={1240}
              className="w-full rounded-lg border border-primary/30 shadow-[0_0_60px_-15px_oklch(0.82_0.13_215/0.3)]"
            />
          </div>
        </div>
      </div>
    </section>
  )
}
