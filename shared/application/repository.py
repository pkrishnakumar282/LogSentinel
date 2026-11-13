"""Bounded SQL queries; latest prediction is selected per event, never double counted."""
from datetime import timedelta
from sqlalchemy import func, select
from shared.pipeline import models as m
from shared.pipeline.store import aware, now


class Repository:
    def __init__(self, engine):
        self.engine = engine

    def ping(self):
        with self.engine.connect() as db:
            db.execute(select(m.training_runs.c.id).limit(1))
        return True

    def logs(self, *, q=None, source=None, service=None, severity=None, since=None, until=None,
             anomaly=None, limit=50, offset=0):
        n, p = m.normalized_logs, m.predictions
        latest = select(p.c.id).where(p.c.event_id == n.c.event_id).order_by(
            p.c.created_at.desc(), p.c.id.desc()).limit(1).correlate(n).scalar_subquery()
        joined = n.outerjoin(p, p.c.id == latest).outerjoin(m.model_versions, m.model_versions.c.id == p.c.model_id)
        conditions = []
        for column, value in ((n.c.source, source), (n.c.service, service), (n.c.severity, severity)):
            if value is not None:
                conditions.append(column == value)
        if q:
            conditions.append(n.c.message.ilike('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))
        if since:
            conditions.append(n.c.timestamp >= since)
        if until:
            conditions.append(n.c.timestamp <= until)
        if anomaly is not None:
            conditions.append(p.c.is_anomaly == anomaly)
        columns = [*n.c, p.c.id.label('prediction_id'), p.c.anomaly_score, p.c.is_anomaly,
                   p.c.context_warm, m.model_versions.c.name.label('model_name'),
                   m.model_versions.c.version.label('model_version')]
        with self.engine.connect() as db:
            total = db.execute(select(func.count()).select_from(joined).where(*conditions)).scalar_one()
            rows = db.execute(select(*columns).select_from(joined).where(*conditions)
                .order_by(n.c.timestamp.desc(), n.c.event_id).limit(limit).offset(offset)).mappings()
            items = [self.public_log(row) for row in rows]
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}

    @staticmethod
    def public_log(row):
        # Never expose fingerprints/internal transport metadata in the browser.
        keys = ('event_id','source','service','hostname','severity','message','template','status_code',
                'prediction_id','anomaly_score','is_anomaly','context_warm','model_name','model_version')
        return {key: row[key] for key in keys} | {'timestamp': aware(row['timestamp']).isoformat(),
            'synthetic': row['record_metadata'].get('synthetic') is True}

    def detail(self, identity):
        with self.engine.connect() as db:
            row = db.execute(select(m.normalized_logs).where(m.normalized_logs.c.event_id == identity)).mappings().first()
            if row is None:
                return None
            history = db.execute(select(m.predictions.c.id, m.predictions.c.anomaly_score,
                m.predictions.c.is_anomaly, m.predictions.c.context_warm, m.predictions.c.created_at,
                m.model_versions.c.name.label('model_name'), m.model_versions.c.version.label('model_version'),
                m.model_versions.c.threshold, m.model_versions.c.scope).join(m.model_versions)
                .where(m.predictions.c.event_id == identity).order_by(m.predictions.c.created_at.desc()).limit(100)).mappings()
            result = {key: row[key] for key in ('event_id','source','service','hostname','severity','message','template','status_code')}
            result.update(timestamp=aware(row['timestamp']).isoformat(), synthetic=row['record_metadata'].get('synthetic') is True,
                          predictions=[dict(item) for item in history], explanation='Scores measure unusualness; they are not probabilities. Feature attribution is unavailable.')
            return result

    def dashboard(self, hours=24):
        n, p = m.normalized_logs, m.predictions
        latest = select(p.c.id).where(p.c.event_id == n.c.event_id).order_by(
            p.c.created_at.desc(), p.c.id.desc()).limit(1).correlate(n).scalar_subquery()
        with self.engine.connect() as db:
            total = db.execute(select(func.count()).select_from(n)).scalar_one()
            scored = dict(db.execute(select(p.c.is_anomaly, func.count()).select_from(n.join(p, p.c.id == latest))
                .group_by(p.c.is_anomaly)).all())
            distributions = {}
            for name, column in [('source', n.c.source), ('severity', n.c.severity)]:
                distributions[name] = [{'name': value, 'count': count} for value, count in db.execute(
                    select(column, func.count()).group_by(column)).all()]
            active = m.alerts.c.status.in_(['open', 'acknowledged'])
            alerts = db.execute(select(func.count()).select_from(m.alerts).where(active)).scalar_one()
            critical = db.execute(select(func.count()).select_from(m.alerts).where(active, m.alerts.c.severity == 'CRITICAL')).scalar_one()
            bucket = func.date_trunc('hour', n.c.timestamp) if db.dialect.name == 'postgresql' else func.strftime('%Y-%m-%dT%H:00:00Z', n.c.timestamp)
            timeline = [{'timestamp': str(time), 'is_anomaly': anomaly, 'count': count} for time, anomaly, count in db.execute(
                select(bucket, p.c.is_anomaly, func.count()).select_from(n.outerjoin(p, p.c.id == latest))
                .where(n.c.timestamp >= now() - timedelta(hours=hours)).group_by(bucket, p.c.is_anomaly).order_by(bucket))]
        return {'total_logs': total, 'normal_logs': scored.get(False, 0), 'anomalies': scored.get(True, 0),
            'unscored_logs': total - sum(scored.values()), 'active_alerts': alerts, 'critical_alerts': critical,
            'distributions': distributions, 'timeline': timeline, 'timeline_hours': hours,
            'recent_events': self.logs(limit=10)['items']}

    def sources(self):
        with self.engine.connect() as db:
            rows = db.execute(select(m.data_sources).order_by(m.data_sources.c.source, m.data_sources.c.hostname)).mappings()
            return {'items': [dict(row) | {'status': 'observed_recently' if aware(row['last_seen']) > now() - timedelta(minutes=5)
                else 'stale', 'status_basis': 'canonical record receipt; not a collector connection probe'} for row in rows]}

    def models(self):
        with self.engine.connect() as db:
            return {'items': [dict(row) for row in db.execute(select(m.model_versions).order_by(m.model_versions.c.created_at.desc()).limit(200)).mappings()],
                    'scope': 'models observed during persisted stream inference; registry is queried separately'}

    def audits(self, limit=50, offset=0, action=None):
        with self.engine.connect() as db:
            query = select(m.audit_events)
            if action:
                query = query.where(m.audit_events.c.action == action)
            return {'items': [dict(row) for row in db.execute(query.order_by(
                m.audit_events.c.created_at.desc(), m.audit_events.c.id).limit(limit).offset(offset)).mappings()]}
