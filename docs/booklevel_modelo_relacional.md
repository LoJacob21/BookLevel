# BookLevel — Modelo Relacional

> Tradução do modelo de domínio para tabelas, tipos, chaves, constraints e índices.
> Alvo: PostgreSQL. Próximo passo após este documento: Django models.
>
> Convenções:
> - Toda PK é `id uuid` com default `gen_random_uuid()`, salvo indicação contrária.
> - `timestamptz` para instantes; `date` para dias de calendário.
> - "enum" = na prática, `text` + `CHECK` (ou tipo ENUM nativo); a escolha final fica para os Django models.
> - Toda tabela tem `created_at timestamptz NOT NULL DEFAULT now()`; tabelas mutáveis também têm `updated_at`. Omitidos abaixo por brevidade, exceto quando relevantes.
> - **MVP** = Onda 1 ou 2. **Futuro** = pós-MVP / Fases 2–5, modelado agora para evitar refatoração.

---

## 1. Identidade e Perfil

### user  *(MVP)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| email | citext | NOT NULL, UNIQUE |
| password_hash | text | NOT NULL |
| nickname | citext | NOT NULL, UNIQUE |
| bio | text | NULL |
| timezone | text | NOT NULL  — IANA tz (ex.: "America/Sao_Paulo") |
| avatar_preset_id | uuid | NULL, FK → avatar_preset(id) |
| total_xp | integer | NOT NULL DEFAULT 0, CHECK (total_xp >= 0)  — **cache** do ledger |
| current_level | smallint | NOT NULL DEFAULT 1, FK → level(level_number)  — **cache** derivado |
| email_verified_at | timestamptz | NULL |
| is_active | boolean | NOT NULL DEFAULT true |

Notas: `total_xp` e `current_level` são projeções materializadas do ledger (ver invariantes). Nunca editados à mão; atualizados na mesma transação que insere `xp_transaction` e reconciliados por job.

### avatar_preset  *(MVP)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| code | text | NOT NULL, UNIQUE |
| name | text | NOT NULL |
| image_url | text | NOT NULL |
| is_active | boolean | NOT NULL DEFAULT true |

---

## 2. Acervo

### author  *(MVP)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| name | text | NOT NULL |
| normalized_name | citext | NOT NULL, UNIQUE  — usado para deduplicar na importação |
| external_id | text | NULL  — id na fonte externa, quando houver |

### genre  *(MVP)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| name | text | NOT NULL |
| normalized_name | citext | NOT NULL, UNIQUE |

### book  *(MVP)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| external_id | text | NULL, UNIQUE  — id no Google Books / Open Library |
| source | text | NOT NULL DEFAULT 'manual'  — enum: google_books / open_library / manual |
| title | text | NOT NULL |
| subtitle | text | NULL |
| total_pages | integer | NOT NULL, CHECK (total_pages > 0) |
| cover_url | text | NULL |
| published_year | smallint | NULL |

### book_author  *(MVP)*  — N:N
| coluna | tipo | constraints |
|---|---|---|
| book_id | uuid | NOT NULL, FK → book(id) ON DELETE CASCADE |
| author_id | uuid | NOT NULL, FK → author(id) |
| PK | | (book_id, author_id) |

### book_genre  *(MVP)*  — N:N
| coluna | tipo | constraints |
|---|---|---|
| book_id | uuid | NOT NULL, FK → book(id) ON DELETE CASCADE |
| genre_id | uuid | NOT NULL, FK → genre(id) |
| PK | | (book_id, genre_id) |

---

## 3. Leitura (loop central)

### user_book  *(MVP)*  — aggregate root
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| book_id | uuid | NOT NULL, FK → book(id) |
| status | text | NOT NULL  — enum: quero_ler / lendo / lido / abandonado |
| current_page | integer | NOT NULL DEFAULT 0, CHECK (current_page >= 0) |
| started_at | timestamptz | NULL  — preenchido quando entra em "lendo" |
| finished_at | timestamptz | NULL  — preenchido quando entra em "lido" |
| | | UNIQUE (user_id, book_id) |

Notas: `current_page <= book.total_pages` não cabe em CHECK (referência cruzada de tabela); validar na aplicação ou via trigger.

### reading_session  *(MVP)*  — quantitativo
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_book_id | uuid | NOT NULL, FK → user_book(id) ON DELETE CASCADE |
| user_id | uuid | NOT NULL, FK → user(id)  — **denormalizado** p/ consultas de streak/estatística |
| start_page | integer | NOT NULL, CHECK (start_page >= 0) |
| end_page | integer | NOT NULL |
| duration_minutes | integer | NULL, CHECK (duration_minutes >= 0) |
| occurred_on | date | NOT NULL  — dia já no fuso do usuário, base da ofensiva |
| | | CHECK (end_page >= start_page) |

### diary_entry  *(MVP)*  — qualitativo
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_book_id | uuid | NOT NULL, FK → user_book(id) ON DELETE CASCADE |
| entry_type | text | NOT NULL DEFAULT 'note'  — enum: note / theory / emotion / milestone |
| mood_tag | text | NULL |
| chapter_label | text | NULL |
| page_at_entry | integer | NULL, CHECK (page_at_entry >= 0) |
| body | text | NOT NULL |
| is_spoiler | boolean | NOT NULL DEFAULT false |
| entry_date | date | NOT NULL |

### review  *(MVP)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_book_id | uuid | NOT NULL, FK → user_book(id) ON DELETE CASCADE, UNIQUE |
| rating | smallint | NOT NULL, CHECK (rating BETWEEN 1 AND 5) |
| body | text | NULL |
| is_spoiler | boolean | NOT NULL DEFAULT false |

Notas: UNIQUE em `user_book_id` garante uma resenha por leitura. Regra "só após status=lido" é validada na aplicação.

### quote  *(MVP — Onda 2)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_book_id | uuid | NOT NULL, FK → user_book(id) ON DELETE CASCADE |
| text | text | NOT NULL |
| page | integer | NULL |
| is_spoiler | boolean | NOT NULL DEFAULT false |

### favorite_character  *(MVP — Onda 2)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_book_id | uuid | NOT NULL, FK → user_book(id) ON DELETE CASCADE |
| name | text | NOT NULL |
| note | text | NULL |

---

## 4. Metas

### goal  *(MVP: só period=daily)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| user_book_id | uuid | NULL, FK → user_book(id) ON DELETE CASCADE  — NULL = meta global |
| metric | text | NOT NULL  — enum: pages / books / minutes |
| period | text | NOT NULL  — enum: daily / weekly / monthly / yearly |
| target_value | integer | NOT NULL, CHECK (target_value > 0) |
| is_active | boolean | NOT NULL DEFAULT true |

Notas: o cumprimento NÃO é guardado aqui — é projeção sobre `reading_session` do período. Precedência (override por livro sobre meta global) resolvida em consulta. Ver índices únicos parciais abaixo.

---

## 5. Progressão e Gamificação

### level  *(MVP)*  — referência, seedada por fórmula
| coluna | tipo | constraints |
|---|---|---|
| level_number | smallint | PK |
| xp_required | integer | NOT NULL, CHECK (xp_required >= 0)  — XP acumulado p/ atingir este nível |
| title | text | NOT NULL  — ex.: "Leitor Iniciante" |

Notas: linhas geradas a partir de `xp_required(n) = base * n^fator` no seed; editáveis depois via Admin.

### xp_transaction  *(MVP)*  — ledger imutável
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| amount | integer | NOT NULL  — pode ser negativo (correção) |
| reason | text | NOT NULL  — enum: pages_read / book_completed / review_written / goal_met / quest_completed / achievement_unlocked / correction |
| source_type | text | NULL  — origem livre (reading_session, review, quest...) |
| source_id | uuid | NULL  — id na origem; **sem FK rígida** (padrão de ledger) |
| created_at | timestamptz | NOT NULL DEFAULT now() |

Notas: imutável — sem UPDATE/DELETE. Recomenda-se reforçar com permissão/trigger que bloqueia UPDATE e DELETE.

### streak  *(MVP — Onda 2)*  — 1:1 com user
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, UNIQUE, FK → user(id) ON DELETE CASCADE |
| current_count | integer | NOT NULL DEFAULT 0, CHECK (current_count >= 0) |
| longest_count | integer | NOT NULL DEFAULT 0, CHECK (longest_count >= current_count) |
| last_active_on | date | NULL  — último dia que contou (fuso do user) |

### streak_freeze  *(MVP — Onda 2)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| used_on | date | NOT NULL |
| period_key | text | NOT NULL  — ex.: "2026-06" p/ impor 1 por mês |
| | | UNIQUE (user_id, period_key)  — 1 recuperação grátis por período |

---

## 6. Missões e Conquistas

### quest  *(MVP — Onda 2)*  — aggregate root
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| code | text | NOT NULL, UNIQUE |
| name | text | NOT NULL |
| description | text | NULL |
| criteria_type | text | NOT NULL  — enum (MVP): pages_read / books_finished / streak_days (futuro: genre_books ...) |
| criteria_value | integer | NOT NULL, CHECK (criteria_value > 0) |
| is_repeatable | boolean | NOT NULL DEFAULT false |
| event_id | uuid | NULL, FK → season_event(id)  — escopo opcional |
| community_id | uuid | NULL, FK → community(id)  — escopo opcional |
| valid_from | timestamptz | NULL |
| valid_until | timestamptz | NULL |
| xp_reward | integer | NOT NULL DEFAULT 0, CHECK (xp_reward >= 0) |

### user_quest  *(MVP — Onda 2)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| quest_id | uuid | NOT NULL, FK → quest(id) |
| status | text | NOT NULL DEFAULT 'in_progress'  — enum: in_progress / completed / expired |
| progress_value | integer | NOT NULL DEFAULT 0, CHECK (progress_value >= 0) |
| completed_at | timestamptz | NULL |

Notas: no máximo um ativo por user+quest — ver índice único parcial. Quest não repetível concluída não volta a in_progress (regra de aplicação + o índice impede duplicar ativo).

### achievement  *(MVP — Onda 2)*  — aggregate root
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| code | text | NOT NULL, UNIQUE |
| name | text | NOT NULL |
| description | text | NULL |
| criteria_type | text | NOT NULL  — passiva (avaliada vs estatística) ou via quest |
| criteria_value | integer | NULL |
| source_quest_id | uuid | NULL, FK → quest(id)  — quando resulta de uma quest |
| event_id | uuid | NULL, FK → season_event(id) |
| xp_reward | integer | NOT NULL DEFAULT 0, CHECK (xp_reward >= 0) |

### user_achievement  *(MVP — Onda 2)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| achievement_id | uuid | NOT NULL, FK → achievement(id) |
| unlocked_at | timestamptz | NOT NULL DEFAULT now() |
| | | UNIQUE (user_id, achievement_id) |

---

## 7. Eventos e Cosméticos  *(Futuro)*

### season_event
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| code | text | NOT NULL, UNIQUE |
| name | text | NOT NULL |
| theme_genre_id | uuid | NULL, FK → genre(id) |
| start_date | date | NOT NULL |
| end_date | date | NOT NULL |
| | | CHECK (end_date >= start_date) |

### cosmetic_reward
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| code | text | NOT NULL, UNIQUE |
| name | text | NOT NULL |
| kind | text | NOT NULL  — enum: avatar_frame / badge / theme ... |
| source_achievement_id | uuid | NULL, FK → achievement(id) |
| event_id | uuid | NULL, FK → season_event(id) |

### user_cosmetic
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| cosmetic_reward_id | uuid | NOT NULL, FK → cosmetic_reward(id) |
| is_equipped | boolean | NOT NULL DEFAULT false |
| acquired_at | timestamptz | NOT NULL DEFAULT now() |
| | | UNIQUE (user_id, cosmetic_reward_id) |

---

## 8. Linha do Tempo e Social

### timeline_event  *(MVP: pessoal)*  — aggregate root
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| type | text | NOT NULL  — enum: book_started / book_finished / level_up / achievement_unlocked / streak_kept / quest_completed / daily_summary |
| payload | jsonb | NOT NULL DEFAULT '{}'  — dados específicos do tipo |
| event_date | date | NOT NULL  — dia (fuso do user); base do resumo diário |
| visibility | text | NOT NULL DEFAULT 'private'  — enum: private / followers / public |
| created_at | timestamptz | NOT NULL DEFAULT now() |

### reaction  *(Futuro)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| timeline_event_id | uuid | NOT NULL, FK → timeline_event(id) ON DELETE CASCADE |
| kind | text | NOT NULL DEFAULT 'like' |
| | | UNIQUE (user_id, timeline_event_id, kind) |

### comment  *(Futuro)*
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| timeline_event_id | uuid | NOT NULL, FK → timeline_event(id) ON DELETE CASCADE |
| body | text | NOT NULL |

### follow  *(Futuro)*
| coluna | tipo | constraints |
|---|---|---|
| follower_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| following_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| created_at | timestamptz | NOT NULL DEFAULT now() |
| PK | | (follower_id, following_id) |
| | | CHECK (follower_id <> following_id) |

---

## 9. Comunidades  *(Futuro — Fase 3)*

### community
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| name | text | NOT NULL |
| slug | citext | NOT NULL, UNIQUE |
| description | text | NULL |

### community_membership
| coluna | tipo | constraints |
|---|---|---|
| id | uuid | PK |
| community_id | uuid | NOT NULL, FK → community(id) ON DELETE CASCADE |
| user_id | uuid | NOT NULL, FK → user(id) ON DELETE CASCADE |
| role | text | NOT NULL DEFAULT 'member'  — enum: member / moderator / owner |
| joined_at | timestamptz | NOT NULL DEFAULT now() |
| | | UNIQUE (community_id, user_id) |

---

## Constraints que materializam invariantes

Resumo das regras de domínio que viram garantias do banco (não dependem só da aplicação):

| Invariante | Como é garantida |
|---|---|
| Um livro por usuário | `UNIQUE (user_id, book_id)` em `user_book` |
| Uma resenha por leitura | `UNIQUE (user_book_id)` em `review` |
| Sessão não reduz progresso | `CHECK (end_page >= start_page)` em `reading_session` |
| Sem duplicação de livro do catálogo | `UNIQUE (external_id)` em `book` |
| Sem duplicação de autor/gênero | `UNIQUE (normalized_name)` |
| XP imutável | sem UPDATE/DELETE (permissão/trigger) |
| Conquista única por usuário | `UNIQUE (user_id, achievement_id)` |
| 1 freeze por período | `UNIQUE (user_id, period_key)` em `streak_freeze` |
| longest >= current | `CHECK` em `streak` |
| 1 quest ativa por user+quest | índice único parcial (abaixo) |
| 1 meta ativa por escopo+tipo+período | índices únicos parciais (abaixo) |
| 1 resumo diário por user/dia | índice único parcial (abaixo) |
| Não seguir a si mesmo | `CHECK (follower_id <> following_id)` |

### Índices únicos parciais (regras condicionais)

```sql
-- No máximo uma quest ativa por par user+quest
CREATE UNIQUE INDEX uq_user_quest_active
  ON user_quest (user_id, quest_id)
  WHERE status = 'in_progress';

-- No máximo uma meta global ativa por user+metric+period
CREATE UNIQUE INDEX uq_goal_global_active
  ON goal (user_id, metric, period)
  WHERE user_book_id IS NULL AND is_active;

-- No máximo uma meta por livro ativa por user_book+metric+period
CREATE UNIQUE INDEX uq_goal_book_active
  ON goal (user_book_id, metric, period)
  WHERE user_book_id IS NOT NULL AND is_active;

-- No máximo um resumo diário por usuário por dia
CREATE UNIQUE INDEX uq_timeline_daily_summary
  ON timeline_event (user_id, event_date)
  WHERE type = 'daily_summary';
```

---

## Índices para os fluxos

Índices que sustentam as consultas dos três fluxos principais e das estatísticas:

```sql
-- Sessões do dia por usuário (ofensiva, meta diária, resumo diário)
CREATE INDEX ix_reading_session_user_day ON reading_session (user_id, occurred_on);

-- Sessões e diário por leitura (revisitar a jornada do livro)
CREATE INDEX ix_reading_session_userbook ON reading_session (user_book_id);
CREATE INDEX ix_diary_entry_userbook_date ON diary_entry (user_book_id, entry_date);

-- Estante por status (montar a biblioteca)
CREATE INDEX ix_user_book_user_status ON user_book (user_id, status);

-- Ledger por usuário (somatório / reconciliação de total_xp)
CREATE INDEX ix_xp_transaction_user ON xp_transaction (user_id, created_at);

-- Timeline do usuário (feed pessoal, ordenado)
CREATE INDEX ix_timeline_user_created ON timeline_event (user_id, created_at DESC);

-- Quests ativas do usuário
CREATE INDEX ix_user_quest_user_status ON user_quest (user_id, status);

-- Resolução de livro na importação
CREATE INDEX ix_book_external ON book (external_id);
```

---

## Decisões registradas

1. **`total_xp` e `current_level` em `user`**: armazenados como **cache** do ledger `xp_transaction`. Atualizados na mesma transação do insert e reconciliados por job Celery. Fonte da verdade continua sendo o ledger.
2. **Níveis**: tabela `level` **seedada a partir de uma fórmula**, editável via Django Admin para balanceamento sem deploy.
3. **Vínculos de ledger/log** (`xp_transaction`, `timeline_event`): `source_type` + `source_id` **sem FK rígida**, para acomodar novas origens sem migração de schema.
4. **Escopos futuros** (`event_id`, `community_id` em quest/achievement): colunas nuláveis já presentes; ativar um evento sazonal ou desafio de comunidade é inserir linhas, não alterar schema.
5. **Direção de dependência entre services** (consolidada na Onda 2): `library → quests → gamification → timeline`. Helpers compartilhados de XP/timeline vivem em `gamification.services`; `quests` lê `library.models` (métricas lifetime) mas nunca importa `library.services` — sem ciclos.
