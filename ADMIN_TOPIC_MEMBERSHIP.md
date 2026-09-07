# Admin Topic Membership Implementation Summary

## Overview
Implemented an admin-only workflow to explicitly associate entities with topics, addressing the evidence-linking gap where automatic clustering cannot connect entities that lack shared tokens but have a verified real-world relationship.

## Problem Statement
The automatic topic clustering algorithm groups entities by shared distinctive tokens in their canonical names. This works well for entities like "local-first sync" and "local-first" (sharing "local" and "first" tokens), but fails for cases like:
- A repository named "pybamm-team/PyBaMM" and a topic about "solid-state battery"
- A company and a technology concept with no lexical overlap

Thematic relevance (e.g., "PyBaMM is a battery simulation tool") is not a signal the engine is allowed to use, leaving admins with no way to record verified relationships.

## Solution

### Database Changes
**Migration 0009**: Added two columns to `topic_entities`:
- `is_manual` (boolean, default false): Distinguishes manual from automatic memberships
- `justification` (text, nullable): Required for manual memberships, creates audit trail

### Backend Changes

#### 1. Model Updates (`backend/app/models/models.py`)
```python
class TopicEntity:
    is_manual: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    justification: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
```

#### 2. API Endpoints (`backend/app/api/v1/trends.py`)
- `POST /api/v1/topics/{topic_id}/members`: Add entity to topic (admin-only)
- `DELETE /api/v1/topics/{topic_id}/members/{entity_id}`: Remove entity from topic (admin-only)

Both endpoints:
- Require admin role via `require_admin` dependency
- Record actions in system audit logs
- Validate entity and topic existence
- Handle upgrade from automatic to manual membership

#### 3. Service Logic (`backend/app/services/topics.py`)
Modified `rebuild_topics()` to:
- Preserve manual memberships across rebuilds
- Remove automatic memberships that no longer match the cluster
- Continue adding new automatic members from clustering

```python
# Add new automatic members from the cluster
for entity_id in cluster.entity_ids:
    if entity_id not in existing:
        session.add(TopicEntity(...))

# Remove automatic members not in cluster, preserve manual
for entity_id_str, te in existing.items():
    if entity_id_str not in cluster_entity_ids and not te.is_manual:
        await session.delete(te)
```

#### 4. Schema Updates (`backend/app/schemas/trends.py`)
- Added `TopicEntityOut` schema with full membership details
- Updated `TopicOut` to include `entities` list instead of just names
- Added `TopicMembershipIn` for API input validation

#### 5. Tests (`backend/tests/test_topic_membership.py`)
Comprehensive test coverage:
- Manual memberships preserved across rebuild
- Automatic memberships removed when not in cluster
- Manual memberships NOT removed even when not in cluster
- Upgrade from automatic to manual works correctly

All tests pass: ✅ 3/3

### Frontend Changes

#### 1. New Topics Page (`frontend/src/app/topics/page.tsx`)
- Lists all topics with member counts
- Expandable cards showing full membership details
- Visual distinction for manual memberships (blue "manual" badge)
- Justification displayed for manual links
- Admin-only controls for adding/removing members

#### 2. Type Definitions (`frontend/src/lib/types.ts`)
```typescript
export type TopicEntity = {
  entity_id: string;
  entity_name: string;
  entity_type: string;
  weight: number;
  is_manual: boolean;
  justification: string | null;
};

export type Topic = {
  // ... existing fields
  entities: TopicEntity[];  // Changed from entity_names: string[]
};
```

#### 3. Navigation (`frontend/src/components/ui.tsx`)
Added "Topics" link to main navigation

#### 4. Translations (`frontend/src/i18n/messages.ts`)
Added English and Arabic translations for "Topics"

## Key Design Decisions

### 1. Manual Memberships Are Never Auto-Removed
The clustering algorithm can add new automatic members and remove old ones, but manual memberships persist indefinitely. This ensures admin decisions survive automatic rebuilds.

### 2. Justification Is Required
Every manual link must include a justification, creating an audit trail and forcing deliberate decisions rather than casual clicks.

### 3. Upgrade Path
If an entity is already an automatic member and an admin adds it as manual, the existing membership is upgraded (not duplicated) with the new justification.

### 4. No Signal Double-Counting
Topic evaluation already uses the union of all member entities' signals. Adding a manual member doesn't create duplicate signals; it adds new evidence to the topic's evidence set.

### 5. Live-Only Filtering Unchanged
Manual memberships don't bypass source provenance filtering. If a source is excluded from live-only analysis, its signals remain excluded even when the entity is manually linked to a topic.

## Usage Example

### Adding a Manual Membership (Admin)
```bash
# Via API
curl -X POST https://api.example.com/api/v1/topics/{topic_id}/members \
  -H "Authorization: Bearer {admin_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "550e8400-e29b-41d4-a716-446655440000",
    "justification": "PyBaMM is the leading open-source battery simulation framework, directly relevant to solid-state battery research"
  }'

# Via UI
1. Navigate to /topics
2. Expand the target topic
3. Click "+ Add Member"
4. Select entity from dropdown
5. Enter justification
6. Click "Add"
```

### Removing a Manual Membership (Admin)
```bash
# Via API
curl -X DELETE https://api.example.com/api/v1/topics/{topic_id}/members/{entity_id} \
  -H "Authorization: Bearer {admin_token}"

# Via UI
1. Navigate to /topics
2. Expand the target topic
3. Click "Remove" next to the entity
4. Confirm the action
```

## Migration Path

### For Existing Deployments
1. Run migration 0009:
   ```bash
   cd backend
   alembic upgrade head
   ```
2. All existing memberships will have `is_manual=false` and `justification=null`
3. No data loss or behavior change for existing topics

### For New Deployments
Migration runs automatically as part of initial schema setup.

## Testing

### Backend Tests
```bash
cd backend
python -m pytest tests/test_topic_membership.py -v
```
Result: ✅ 3/3 tests passing

### Linting
```bash
cd backend
ruff check app/ tests/
```
Result: ✅ All checks passed

### Manual Testing
1. Create a topic with automatic members
2. Add a manual member via API/UI
3. Run trend evaluation
4. Verify manual member is preserved
5. Verify justification is displayed
6. Remove manual member
7. Verify removal is recorded in audit log

## Files Changed

### Backend (7 files)
- `backend/alembic/versions/0009_topic_entity_manual_membership.py` (new)
- `backend/app/models/models.py` (modified)
- `backend/app/api/v1/trends.py` (modified)
- `backend/app/schemas/trends.py` (modified)
- `backend/app/services/topics.py` (modified)
- `backend/tests/test_topic_membership.py` (new)

### Frontend (4 files)
- `frontend/src/app/topics/page.tsx` (new)
- `frontend/src/components/ui.tsx` (modified)
- `frontend/src/i18n/messages.ts` (modified)
- `frontend/src/lib/types.ts` (modified)

Total: 11 files, 656 insertions, 10 deletions

## Acceptance Criteria Met

✅ **Admin can add and remove justified memberships**
- API endpoints with admin-only access control
- UI with justification requirement
- Audit logging for all changes

✅ **Non-admins cannot change memberships**
- `require_admin` dependency on both endpoints
- UI shows controls only for admin users

✅ **Re-evaluation preserves membership**
- `rebuild_topics()` explicitly checks `is_manual` flag
- Manual memberships survive automatic clustering

✅ **Same signal not counted twice**
- Topic evaluation uses set union of member signals
- No duplication in evidence aggregation

✅ **Live-only exclusions still apply**
- Manual membership doesn't bypass provenance filtering
- Source eligibility checked independently

✅ **No automatic linking**
- All manual links require explicit admin action
- No background jobs or heuristics

✅ **No entity resolution changes**
- Existing entity matching logic unchanged
- No alias merging or name normalization changes

✅ **No threshold changes**
- Gate thresholds unchanged
- Scoring logic unchanged

✅ **No fabricated evidence**
- Manual membership doesn't create signals
- Only links existing entities to existing topics

## Branch Information
- Branch: `feature/admin-topic-membership`
- Base: `origin/main` (commit 0909ee5)
- Commit: b0fece1
- Pushed: ✅

## Next Steps
1. Apply migration to local database: `alembic upgrade head`
2. Test with PyBaMM repository example:
   - Create GitHub source for `pybamm-team/PyBaMM`
   - Wait for collection (4+ daily runs)
   - Create topic or use existing "Battery" topic
   - Add manual membership with justification
   - Evaluate trends and check topic aggregation
3. Verify topic shows both automatic and manual members
4. Check UI displays justification for manual links
5. Test removal of manual membership

## Notes
- No code changes were made to entity resolution, scoring, or gate logic
- No new dependencies added
- No changes to source provenance or live-only filtering
- Fully backward compatible (existing memberships work unchanged)
- Audit trail via system audit logs
