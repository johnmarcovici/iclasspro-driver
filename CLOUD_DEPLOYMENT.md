# Cloud Deployment Guide for iClassPro Multi-User Dashboard

This guide covers deploying the iClassPro Dashboard to Google Cloud Platform for multi-user, always-on hosting.

## Architecture Overview

- **Web Service:** Google Cloud Run (managed, auto-scaling)
- **Database:** Cloud SQL for PostgreSQL
- **Secrets:** Google Secret Manager
- **Storage:** Cloud Storage for job artifacts (optional)
- **Networking:** Cloud Armor for DDoS protection, custom domain with managed SSL

## Prerequisites

- Google Cloud Project with billing enabled
- `gcloud` CLI installed and authenticated
- Docker installed locally
- PostgreSQL CLI tools (optional, for local testing)

## Step 1: Create Cloud SQL Database

```bash
# Set project
export PROJECT_ID=your-project-id
export REGION=us-central1
gcloud config set project $PROJECT_ID

# Create Cloud SQL PostgreSQL instance
gcloud sql instances create iclasspro-db \
  --database-version=POSTGRES_15 \
  --region=$REGION \
  --tier=db-custom-2-8192 \
  --availability-type=REGIONAL \
  --backup-start-time=03:00 \
  --enable-bin-log

# Create database
gcloud sql databases create iclasspro_db --instance=iclasspro-db

# Create database user
gcloud sql users create iclasspro_app \
  --instance=iclasspro-db \
  --password=$(openssl rand -base64 32)

# Get the connection string
gcloud sql instances describe iclasspro-db --format='value(connectionName)'
```

## Step 2: Configure Secrets in Secret Manager

```bash
# Generate session secret
SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Generate encryption key
ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Create secrets
echo -n "$SESSION_SECRET" | gcloud secrets create dashboard-session-secret --data-file=-
echo -n "$ENCRYPTION_KEY" | gcloud secrets create encryption-key --data-file=-
echo -n "postgresql://iclasspro_app:PASSWORD@CLOUD_SQL_HOST/iclasspro_db" | \
  gcloud secrets create database-url --data-file=-

# Grant Cloud Run access to secrets
gcloud secrets add-iam-policy-binding dashboard-session-secret \
  --member=serviceAccount:PROJECT_ID@appspot.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor

gcloud secrets add-iam-policy-binding encryption-key \
  --member=serviceAccount:PROJECT_ID@appspot.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor

gcloud secrets add-iam-policy-binding database-url \
  --member=serviceAccount:PROJECT_ID@appspot.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
```

## Step 3: Build and Push Docker Image

```bash
# Configure Docker authentication
gcloud auth configure-docker

# Build the Docker image
docker build -t gcr.io/$PROJECT_ID/iclasspro-dashboard:latest .

# Push to Container Registry
docker push gcr.io/$PROJECT_ID/iclasspro-dashboard:latest
```

## Step 4: Deploy to Cloud Run

```bash
# Create Cloud Run service
gcloud run deploy iclasspro-dashboard \
  --image gcr.io/$PROJECT_ID/iclasspro-dashboard:latest \
  --region $REGION \
  --platform managed \
  --memory 1Gi \
  --cpu 2 \
  --timeout 3600 \
  --max-instances 10 \
  --min-instances 1 \
  --no-allow-unauthenticated \
  --set-env-vars "ENVIRONMENT=production,MAX_CONCURRENT_JOBS=5,MAX_JOBS_PER_USER=3,COOKIE_SECURE=1" \
  --set-secrets "DATABASE_URL=database-url:latest,DASHBOARD_SESSION_SECRET=dashboard-session-secret:latest,ENCRYPTION_KEY=encryption-key:latest" \
  --vpc-connector default \
  --ingress internal
```

## Step 5: Configure Identity-Aware Proxy (IAP)

```bash
# Enable IAP API
gcloud services enable iap.googleapis.com

# Create OAuth consent screen and credentials (via Cloud Console)
# Then configure IAP for the Cloud Run service

gcloud run services set-iam-policy iclasspro-dashboard \
  --policy=policy.yaml
```

## Step 6: Set Up Custom Domain

```bash
# Add your domain to Cloud Run
gcloud run domain-mappings create \
  --service=iclasspro-dashboard \
  --domain=enrollment.example.com \
  --region=$REGION

# Verify DNS records and SSL certificate (automatic with managed certificates)
```

## Step 7: Database Initialization

```bash
# Connect to Cloud SQL and initialize schema
# Using Cloud SQL Auth Proxy:
cloud_sql_proxy -instances=PROJECT_ID:REGION:iclasspro-db &

# Then run:
psql -h 127.0.0.1 -U iclasspro_app -d iclasspro_db < schema.sql
```

## Step 8: Configure Cloud Armor

```bash
# Create security policy
gcloud compute security-policies create iclasspro-policy \
  --type=CLOUD_ARMOR

# Add rate limiting rule
gcloud compute security-policies rules create 100 \
  --security-policy=iclasspro-policy \
  --action=rate-based-ban \
  --rate-limit-options=enforce-on-key=IP,ban-duration-sec=600,conform-action=allow,exceed-action=deny-429,rate-limit-threshold-count=100,rate-limit-threshold-interval-sec=60

# Add allowed countries rule
gcloud compute security-policies rules create 200 \
  --security-policy=iclasspro-policy \
  --action=allow \
  --description="Allow traffic from US"
```

## Monitoring and Logging

### Cloud Logging

```bash
# View application logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=iclasspro-dashboard" \
  --limit 50 --format json

# Create log sink for errors
gcloud logging sinks create iclasspro-errors \
  logging.googleapis.com/projects/$PROJECT_ID/logs/iclasspro-errors \
  --log-filter='resource.type="cloud_run_revision" AND severity=ERROR'
```

### Cloud Monitoring

```bash
# Create alerts for error rate
gcloud monitoring policies create \
  --notification-channels=CHANNEL_ID \
  --display-name="iClassPro High Error Rate" \
  --condition-display-name="Error rate > 5%" \
  --condition-threshold-value=0.05
```

## Backup and Disaster Recovery

```bash
# Enable automated backups (already done in step 1)
# Verify backup configuration
gcloud sql backups list --instance=iclasspro-db

# Create manual backup
gcloud sql backups create --instance=iclasspro-db

# Test restore to point-in-time
# (Only in staging environment)
gcloud sql backups restore BACKUP_ID --backup-instance=iclasspro-db --backup-configuration=xxxxxx
```

## Cost Optimization

- Use Cloud SQL custom tier to match actual needs
- Set Cloud Run min instances to 0 for dev/test, 1+ for prod
- Enable Cloud CDN for static assets
- Monitor and adjust MAX_CONCURRENT_JOBS based on load testing

## Troubleshooting

### Database Connection Issues

```bash
# Test connection from local machine
gcloud sql connect iclasspro-db --user=iclasspro_app

# Check Cloud Run service account permissions on Secret Manager
gcloud secrets get-iam-policy database-url
```

### High Memory Usage

- Reduce MAX_CONCURRENT_JOBS
- Increase Cloud Run memory allocation
- Profile with Cloud Profiler

### Job Timeouts

- Cloud Run timeout is set to 3600s (1 hour)
- For longer jobs, implement job streaming with Cloud Tasks

## Post-Deployment Checklist

- [ ] Database backups configured and tested
- [ ] Secrets properly secured and rotated
- [ ] Cloud Armor policies deployed
- [ ] Custom domain configured with HTTPS
- [ ] Monitoring alerts set up
- [ ] Log retention configured
- [ ] Disaster recovery plan documented
- [ ] Load testing completed
- [ ] Security audit passed
- [ ] Cost estimates reviewed

## Rollback Procedure

```bash
# Revert to previous image version
gcloud run deploy iclasspro-dashboard \
  --image gcr.io/$PROJECT_ID/iclasspro-dashboard:PREVIOUS_VERSION \
  --region $REGION

# Or use Traffic Splitting
gcloud run services update-traffic iclasspro-dashboard \
  --to-revisions LATEST=90,PREVIOUS=10 \
  --region $REGION
```

## Next Steps

1. **Job Queue Extraction:** Move long-running jobs to Cloud Tasks + separate worker service
2. **Email Notifications:** Integrate Cloud Sendgrid/SendGrid for completion notifications
3. **Advanced Analytics:** Add BigQuery for job analytics and performance tracking
4. **Multi-Region:** Deploy to multiple regions with Cloud Load Balancing
