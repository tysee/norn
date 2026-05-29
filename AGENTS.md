---
alwaysApply: true
---

# General

!!! VERY IMPORTANT: Use context7 mcp server for all expo and react native related tasks.
!!! IMPORTANT: Use english language for all user facing text in app UI. In chat with language of the user.
!!! IMPORTANT: Contract first approach. Always think about the contract first before implementing anything.

**CRITICAL**: if you not agree with some rules or prompt, ask for confirmation before implementing. Try to find best solution for the task if you not agree with the prompt.

## Project

See docs in `docs` folder. As primary source of truth for the project. If you change some prinicipal architecture, design, or any other important decision, update the docs accordingly.

## Developer Mindset

- Act as Senior Engineer, and Senior Developer with 10x engineer approach
- Think deeply before implementing features. And do not hurry with conclusions.
- Don't settle for simple and wrong decisions from prompts. Ask for clarification if needed. But don't start arguing just because of that.
- Complete entire implementation before stopping.
- Prefer fewer lines of code with higher quality instead of long and complex code.
- "Do not reinvent the wheel" use the best tools and libraries for the job. Don't try to build things from scratch.
- **ALWAYS check already implemented features before implementing new ones.**
- Never commit code or interact with git by yourself. Ask for review and approval from human.

### Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.

### Simplicity First

Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

## Architecture Principles

- Feature-based architecture
- SOLID principles
- Clean code
- RESTful API
- GRPC between backend services

## Code Quality Standards

- Update documentation after changes - describe dependencies and methods
- Remove unused commented code instead of leaving it
- All user-facing output, logs, and errors in English
- Comments and documentation in Russian
- Use linters and formatters to maintain code quality: `bun lint` and `bun typecheck`
- ALWAYS run both `bun lint` and `bun typecheck` before completing any task

## Documentation

Add file path comments at top of files: `// /features/explore/main-screen.tsxy` and short description why this file is needed, what it does and methods it has.

Example:

```text
/*

 /features/explore/main-screen.tsxy

 This is the main screen of the explore screen
 It displays a list of workouts and allows the user to navigate to the workout details screen.

 Methods:
 - getWorkouts() - get list of workouts
 - navigateToWorkoutDetails(workoutId: string) - navigate to the workout details screen
*/
```

Add section comments to the code to semplyfy reading structure of the screen elements and components:

Example:

```text
{/* Security and Privacy Section */}
      <ProfileSection title={ProfileMessages.profile.sections.securityPrivacy}>
        <ProfileCard>
          {SECURITY_MENU_ITEMS.map((item, index) => (
            <ProfileCardItem
              key={item.id}
              title={item.title}
              icon={item.icon}
              onPress={navigateToPrivacyPolicy}
              showArrow={true}
              isLastItem={index === SECURITY_MENU_ITEMS.length - 1}
            />
          ))}
        </ProfileCard>
      </ProfileSection>
```

Store all documentation in the `@docs` folder.  
Documentation must **only** describe **finalized and implemented features**.  
Any conceptual, draft, or audit-type documentation is **strictly prohibited**.

```text
.
├── @docs
│   ├── architecture
│   ├── features
│   └── technologies
│   └── ui
│   └── tmp 
```

### Documentation Rules

- Documentation exists only for finalized, production-ready features.
- Do not create documents for drafts, research, audits, reviews, or summaries.

### Documentation Structure

- Always update existing documentation for a feature instead of creating a new file.
- If the documentation for a feature does not yet exist — ask for confirmation before creating it.
- Do not add new folders or files unless explicitly approved.

### Documentation Update Process

- Always update existing documentation for a feature instead of creating a new file.
- If the documentation for a feature does not yet exist — ask for confirmation before creating it.
- Do not add new folders or files unless explicitly approved.
- Use `@docs/tmp` folder for temporary documentation.

### Update Process

Before implementation:

- Read existing internal and external documentation.
- Ask for clarification if anything is unclear.

After implementation:

- Update the relevant feature’s documentation with final information.
- Do not generate any new documents unless explicitly requested.

## Tech Stack

Frontend:

- Typescript (latest version)
- Expo (all features from latest expo will be used) - <https://docs.expo.dev/llms.txt>
- Custom lightweight theme system with React Context API for theming
- Feature-based architecture with reusable UI components in `lib/ui/`

**ALWAYS use EXPO components and libraries** instead of other react native libraries from community.

Backend:

- ClickHouse
- PostgreSQL
- Dbt
- Docker (for local development)
- Node.js (latest version)
- Python (latest version)
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- pytest
- pytest-asyncio
- pytest-mock
- pytest-cov
- pytest-sugar
