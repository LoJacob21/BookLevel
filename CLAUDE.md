# BookLevel — Contexto do Projeto

## Papel do Claude Code

Você é responsável por implementar o BookLevel seguindo fielmente a arquitetura e o domínio já definidos.

Prioridades:

1. Implementar corretamente.
2. Manter consistência com os documentos de referência.
3. Perguntar antes de alterar decisões arquiteturais.
4. Preferir código simples, legível e idiomático do Django.

## O que é

BookLevel é um "RPG de Leitura": plataforma que gamifica a experiência de leitura
com XP, níveis, conquistas, quests, ofensiva (streak) e uma timeline pessoal da
jornada do usuário. Não é apenas um catálogo de livros nem uma rede social pura —
o produto deve ser sentido como uma evolução de personagem.

## Como trabalhar

Ao implementar novas funcionalidades:

- Leia primeiro os documentos de referência.
- Explique rapidamente o plano antes de modificar muitos arquivos.
- Faça alterações pequenas e coesas.
- Após concluir uma etapa, aguarde revisão antes de seguir para a próxima.

## Stack

- Backend: Python + Django + Django REST Framework
- Banco: PostgreSQL (use recursos específicos do Postgres quando fizer sentido —
  ex.: índices únicos parciais — já assumimos essa dependência conscientemente)
- Cache / broker: Redis
- Jobs assíncronos: Celery + Celery Beat
- Idioma do projeto: nomes de código em inglês; comunicação e commits em português

## Fonte de verdade

Antes de criar ou alterar qualquer model, app ou migração, consulte:

- `docs/booklevel_modelo_relacional.md` — schema completo: tabelas, tipos, PK/FK,
  constraints, índices. Toda tabela e toda constraint já foi decidida ali.
- `docs/booklevel_django_models.md` — tradução já feita desse schema para Django
  models, app por app, com a ordem de implementação recomendada.

Esses dois documentos são o resultado de uma fase de modelagem de domínio já
fechada e validada. **Não redesenhe o domínio.** Se notar algo que parece um
problema na modelagem, pare e pergunte antes de improvisar uma solução diferente
— pode ser uma decisão consciente (ver seção "Decisões que parecem estranhas
mas são propositais" abaixo).

## Regras inegociáveis

1. **AUTH_USER_MODEL custom.** O projeto usa `accounts.User` (email como login,
   nickname como handle público) desde a primeira migração. Nunca usar o User
   padrão do Django.
2. **Toda escrita passa pelo backend Django.** Nenhuma regra de negócio ou
   gravação acontece fora dele — nem via API automática de provedores de banco,
   nem via cliente direto ao Postgres. Se algo futuro envolver Supabase, é
   apenas como infraestrutura (banco gerenciado / storage), nunca como porta de
   escrita paralela.
3. **XPTransaction é um ledger imutável.** Nunca fazer UPDATE ou DELETE numa
   linha existente. Correções de XP são sempre uma nova transação (inclusive
   com `amount` negativo).
4. **`User.total_xp` e `User.current_level` são caches**, não a fonte da
   verdade — a verdade é a soma de `XPTransaction`. Toda alteração de XP deve:
   (a) criar a `XPTransaction`, e (b) atualizar o cache no `User`, **na mesma
   transação de banco** (`transaction.atomic()`). Nunca atualizar um sem o outro.
5. **Sem FK rígida em campos de ledger/log** (`XPTransaction.source_id`,
   eventos similares). Isso é proposital — não "corrija" adicionando uma FK.
6. **Validações cross-table ficam na aplicação, não em CHECK constraints**
   (ex.: `UserBook.current_page <= Book.total_pages`). Decisão consciente,
   documentada no modelo relacional.
7. Nunca criar código "temporário" ou "placeholder" sem deixar explícito
   que é provisório. Se uma decisão depender de uma informação ausente,
   pergunte antes de assumir.

## Decisões que parecem estranhas mas são propositais

- `ReadingSession.user_id` é denormalizado (repete o que já dá pra obter via
  `user_book.user_id`). É proposital, para evitar JOIN nas consultas mais
  quentes do sistema (ofensiva, meta diária, estatísticas).
- `Level` é uma tabela, não uma fórmula em runtime — seedada a partir de uma
  fórmula, mas editável depois via Django Admin, para permitir rebalanceamento
  sem deploy.
- `Quest` e `Achievement` têm `event_id`/`community_id` nuláveis mesmo sem os
  apps `events`/`communities` existirem ainda — preparação intencional para
  Eventos Sazonais e Comunidades, sem exigir migração estrutural depois.
- Índices únicos parciais (`UniqueConstraint(..., condition=Q(...))`) são
  usados de propósito para regras condicionais (ex.: "uma quest ativa por
  vez", "um resumo de timeline por dia"). Não são erro nem complexidade
  acidental.

## Ordem de implementação

Seguir a ordem de apps definida em `docs/booklevel_django_models.md` (seção
"Ordem de implementação sugerida"). Não pular para apps de gamificação antes
de `accounts`, `catalog` e `library` estarem migrados e estáveis.

## Convenções de código

- Um app Django por bounded context do domínio (`accounts`, `catalog`,
  `library`, `goals`, `gamification`, `quests`, `timeline`, depois `events` e
  `communities`).
- Toda PK é UUID (`default=uuid.uuid4, editable=False`).
- Regras de negócio que orquestram múltiplas escritas (ex.: conceder XP,
  avaliar conquistas, gerar evento de timeline) vivem em `services.py` dentro
  do app correspondente — não em views nem em métodos de model que escondem
  efeitos colaterais grandes.
- Services podem chamar outros services quando fizer sentido, mas devem evitar dependências circulares entre apps.
- Após `makemigrations`, sempre mostrar a migração gerada antes de aplicar
  `migrate`, para revisão.
- Nunca apagar ou editar migrations já versionadas sem perguntar.

## Qualidade do código

- Utilize type hints quando apropriado.
- Evite duplicação de código.
- Prefira composição à herança.
- Escreva código idiomático do Django.
- Explique decisões não óbvias com comentários curtos.
- Utilize os padrões do Django e do DRF (ViewSets, Serializers, etc.).
- Respeite as convenções de nomenclatura do Django e do Python.

## O que NÃO fazer sem perguntar

- Não alterar `AUTH_USER_MODEL` depois de definido.
- Não adicionar FK rígida onde o modelo definiu ledger sem FK.
- Não remover ou flexibilizar a imutabilidade de `XPTransaction`.
- Não implementar Timeline Social, Eventos Sazonais ou Comunidades antes do
  MVP (Onda 1 + Onda 2) estar completo — essas entidades existem no schema
  por preparação, não para implementação imediata.
