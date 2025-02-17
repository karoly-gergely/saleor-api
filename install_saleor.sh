#!/bin/bash

# Exit immediately on error
set -e

# Functions
show_progress() {
  local duration=$1
  local steps=20
  local increment=$((duration / steps))

  echo -n "["
  for ((i = 0; i < steps; i++)); do
    echo -n "="
    sleep "$increment"
  done
  echo "]"
}

echo "***********************************************"
echo "*         Saleor Production Installer         *"
echo "***********************************************"
echo

# Prompt for environment variables if not already set
read -p "Enter your domain name (e.g., example.com): " domain_name
read -p "Enter your docker user (e.g., mike92): " docker_user
read -p "Enter a secure password for PostgreSQL (or press Enter to auto-generate): " postgres_password

if [ ! -d "/var/lib/saleor/" ]; then
  mkdir /var/lib/saleor/;
fi
cd /var/lib/saleor/;

# Generate a random password if not provided
if [[ -z "$postgres_password" ]]; then
  postgres_password=$(openssl rand -base64 12)
  echo "Generated PostgreSQL password: $postgres_password"
fi

# Prepare Saleor setup directory
show_progress 2
if [ ! -d "saleor" ]; then
  echo "Cloning the latest stable Saleor backend (API)..."
  git clone --depth 1 https://github.com/saleor/saleor.git saleor
fi

if [ ! -d "saleor-dashboard" ]; then
  echo "Cloning Saleor dashboard..."
  git clone --depth 1 https://github.com/saleor/saleor-dashboard.git saleor-dashboard
fi

if [ ! -d "saleor-storefront" ]; then
  echo "Cloning Saleor storefront..."
  git clone --depth 1 https://github.com/saleor/saleor-storefront.git saleor-storefront
fi

# Check if Docker network exists, create it if it doesn't
show_progress 2
if ! docker network ls | grep -q "saleor-network"; then
  echo "Creating Docker network..."
  docker network create --driver overlay --attachable saleor-network
fi

# Set up SSL key
show_progress 1
if [ ! -f "my-private-key.pem" ]; then
  echo "Creating SSL key..."
  openssl genrsa -out my-private-key.pem 3072
  echo "" >> my-private-key.pem
fi

# Set up the docker-compose.yml file
show_progress 1
if [ ! -f "docker-compose.yml" ]; then
  echo "Creating docker-compose.yml for production..."
  cat <<EOF > docker-compose.yml
version: '3.8'

services:
  api:
    stdin_open: true
    tty: true
    image: ${docker_user}/saleor-api:latest
    environment:
      - DATABASE_URL=postgres://doadmin:$postgres_password@db-postgresql-nyc1-53815-do-user-9240744-0.m.db.ondigitalocean.com:25060/defaultdb
      - REDIS_URL=redis://redis:6379/0
      - SECRET_KEY=$(openssl rand -base64 12)
      - ALLOWED_CLIENT_HOSTS=$domain_name,localhost,127.0.0.1
      - ALLOWED_HOSTS=*
      - RSA_PRIVATE_KEY
      - DEBUG=True
      - PUBLIC_URL=https://$domain_name
      - EMAIL_URL=smtp://mailpit:1025
      - USER_EMAIL_URL=smtp://mailpit:1025
      - EMAIL_HOST=mailpit
      - EMAIL_PORT=1025
      - EMAIL_HOST_USER=
      - EMAIL_HOST_PASSWORD=
      - EMAIL_USE_TLS=False
      - EMAIL_USE_SSL=False
      - DEFAULT_FROM_EMAIL=noreply@$domain_name
      - DEFAULT_FROM_NAME=Noreply TDH
      - ENABLE_ACCOUNT_CONFIRMATION_BY_EMAIL=True
      - CELERY_BROKER_URL=redis://redis:6379/1
    networks:
      - saleor-network
    expose:
      - "8000"
    volumes:
      # shared volume between worker and api for media
      - saleor-media:/app/media

  dashboard:
    image: ghcr.io/saleor/saleor-dashboard:latest
    environment:
      - API_URL=https://$domain_name/graphql/
      - BASE_URL=https://$domain_name/
      - ALLOWED_CLIENT_HOSTS=$domain_name,localhost,127.0.0.1
      - ALLOWED_HOSTS=*
    networks:
      - saleor-network
    expose:
      - "9000"

#  storefront:
#    image: ghcr.io/saleor/saleor-storefront:latest
#    networks:
#      - saleor-network

  redis:
    image: redis:alpine
    ports:
      - 6379:6379
    restart: unless-stopped
    networks:
      - saleor-network
    volumes:
      - saleor-redis:/data

  caddy:
    image: caddy:latest
    networks:
      - saleor-network
    environment:
      - ACME_AGREE=true
    volumes:
      - caddy_data:/data  # Persist certificates
      - caddy_config:/config
      - ./Caddyfile:/etc/caddy/Caddyfile  # Caddy configuration file
    ports:
      - "80:80"    # HTTP, for Let's Encrypt challenge
      - "443:443"  # HTTPS

  worker:
    image: ${docker_user}/saleor-api:latest
    command: celery -A saleor --app=saleor.celeryconf:app worker --loglevel=info -B
    restart: unless-stopped
    networks:
      - saleor-network
    environment:
      - DATABASE_URL=postgres://doadmin:$postgres_password@db-postgresql-nyc1-53815-do-user-9240744-0.m.db.ondigitalocean.com:25060/defaultdb
      - REDIS_URL=redis://redis:6379/0
      - SECRET_KEY=$(openssl rand -base64 12)
      - ALLOWED_CLIENT_HOSTS=$domain_name,localhost,127.0.0.1
      - ALLOWED_HOSTS=*
      - RSA_PRIVATE_KEY
      - DEBUG=True
      - PUBLIC_URL=https://$domain_name
      - EMAIL_URL=smtp://mailpit:1025
      - USER_EMAIL_URL=smtp://mailpit:1025
      - DEFAULT_FROM_EMAIL=noreply@$domain_name
      - DEFAULT_FROM_NAME=Noreply TDH
      - ENABLE_ACCOUNT_CONFIRMATION_BY_EMAIL=True
      - CELERY_BROKER_URL=redis://redis:6379/1
      - EMAIL_HOST=mailpit
      - EMAIL_PORT=1025
      - EMAIL_HOST_USER=
      - EMAIL_HOST_PASSWORD=
      - EMAIL_USE_TLS=False
      - EMAIL_USE_SSL=False
    depends_on:
      - redis
      - mailpit
    volumes:
      # shared volume between worker and api for media
      - saleor-media:/app/media

  jaeger:
    image: jaegertracing/all-in-one
    ports:
      - "5775:5775/udp"
      - "6831:6831/udp"
      - "6832:6832/udp"
      - "5778:5778"
      - "16686:16686"
      - "14268:14268"
      - "9411:9411"
    restart: unless-stopped
    networks:
      - saleor-network
    volumes:
      - type: tmpfs
        target: /tmp

  mailpit:
    image: axllent/mailpit
    ports:
      - 1025:1025 # smtp server
      - 8025:8025 # web ui. Visit http://localhost:8025/ to check emails
    restart: unless-stopped
    networks:
      - saleor-network

networks:
  saleor-network:
    driver: bridge

volumes:
  caddy_data:
  caddy_config:
  saleor-media:
  saleor-redis:
    driver: local
EOF
fi

if [ ! -f "Caddyfile" ]; then
  echo "Creating Caddyfile..."

  cat <<EOF > Caddyfile
$domain_name {
    # encode gzip

    # Route all /graphql/* requests to the API service
    reverse_proxy /graphql/* api:8000
    reverse_proxy /thumbnail/* api:8000
    reverse_proxy /media/* api:8000

    # Route all other requests to the dashboard service
    reverse_proxy dashboard:80 {
        header_up *
        header_down *
    }

    # Set MIME types for JavaScript and CSS files
    @js_files path_regexp js_files ^/.*\.js$
    header @js_files Content-Type application/javascript

    @css_files path_regexp css_files ^/.*\.css$
    header @css_files Content-Type text/css

    tls {
        on_demand
    }
}
EOF
fi

# Set up environment files if not already present
show_progress 1
if [ ! -f "saleor/.env" ]; then
  echo "Configuring backend environment variables..."

  cat <<EOF > saleor/.env
SECRET_KEY=$(openssl rand -base64 32)
ALLOWED_HOSTS=$domain_name
DEBUG=True
DATABASE_URL=postgres://doadmin:$postgres_password@db-postgresql-nyc1-53815-do-user-9240744-0.m.db.ondigitalocean.com:25060/defaultdb
REDIS_URL=redis://redis:6379/0
EOF
fi

if [ ! -f "saleor-dashboard/.env" ]; then
  echo "Setting up saleor-dashboard .env file..."
  cp saleor-dashboard/.env.template saleor-dashboard/.env

  # Update API_URL and BASE_URL
  sed -i "s|^API_URL=.*|API_URL=https://$domain_name/graphql/|" saleor-dashboard/.env  # Use 'api' as the hostname
  sed -i "s|BASE_URL=.*|BASE_URL=https://$domain_name/|" saleor-dashboard/.env  # Set BASE_URL for dashboard
fi

# Install Docker and Docker Compose if not already installed
show_progress 4
if ! command -v docker &> /dev/null; then
  echo "Installing Docker..."
  sudo apt update && sudo apt install -y docker.io
  sudo systemctl start docker
  sudo systemctl enable docker
fi

if ! command -v docker-compose &> /dev/null; then
  echo "Installing Docker Compose..."
  sudo curl -L "https://github.com/docker/compose/releases/download/$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep -Po '(?<="tag_name": ")[^"]*')" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose
fi

# Deploying services using Docker Compose
echo "Deploying Saleor services..."
show_progress 3
echo "dckr_pat_1yPwQEIxy836J3rZCRIhEQIkjSU" | docker login -u kg97 --password-stdin
docker-compose --project-directory . down --remove-orphans && docker-compose pull && export RSA_PRIVATE_KEY=$(cat /var/lib/saleor/my-private-key.pem) && docker-compose up -d --build

# Run migrations and collect static files
echo "Running migrations and collecting static files..."
show_progress 2
docker-compose exec api python3 manage.py migrate
docker-compose exec api python3 manage.py collectstatic --noinput

#export TOKEN="eyJhbGciOiJSUzI1NiIsImtpZCI6Im9ycE9uZG9GVTY2VE5CRUIyNDMxcTlwSmNNQ1FoWlE3VE1OUTlRd0ZtWVkiLCJ0eXAiOiJKV1QifQ.eyJpYXQiOjE3Mjk4NzE5NTQsIm93bmVyIjoic2FsZW9yIiwiaXNzIjoiaHR0cDovL2xvY2FsaG9zdDo4MDAwL2dyYXBocWwvIiwiZXhwIjoxNzI5ODcyMjU0LCJ0b2tlbiI6IlZLVW1RVDdzYmdWTCIsImVtYWlsIjoia2Fyb2x5LmdlcmdlbHlAc3BpZGVybGlua2VkLmNvbSIsInR5cGUiOiJhY2Nlc3MiLCJ1c2VyX2lkIjoiVlhObGNqb3giLCJpc19zdGFmZiI6dHJ1ZX0.UUibZxMIALY-D8XTUxtlAHb2iqeqRhtgvNJ80IbQhxRjdZLJPYJXkUQhJu_lz6jXuZewdOsBJs-NvG4pHpofY9o_y-0wAp3V5nni1wJLe156EJsUS3nV7IbtspLPBW_fapjFC8jSLQ12Er3y5iCSsFWZeaWFbjy96Ug0wWwQFaQFEV-nMxzEtq_uKP97s2n24XqDc9LujFQO0GJSLVK1WHpcUJTjrk8dtA35mQI27UkGCM9rI_uOs2Tn15T0gmtHBT48kLLYojR5bqp-t-dVxc3Q20XZ6O89KobAsQS_RGos9JzQuRJfxVO8TiqNJTAjm4UJaa2ZdWmj1Xu7fPwWyQ"
# set the name and domain in the mutation
#curl 'http://198.211.108.107:8000/graphql/' \
#    -X POST \
#	-H 'Content-Type: application/json' \
#	-H "authorization-bearer: $TOKEN" \
#    --data-raw '{"operationName":"ShopDomainUpdate","variables":{"input":{"name":"saleor","domain":"198.211.108.107:8000"}},"query":"mutation ShopDomainUpdate($input:SiteDomainInput!){shopDomainUpdate(input:$input){__typename}}"}'

# Display access information
echo
echo "***********************************************"
echo "*      Saleor Production Installation Completed!         *"
echo "***********************************************"
echo
echo "Access your Saleor instance at the following addresses:"
echo "Storefront: https://$domain_name:3000/"
echo "Dashboard: https://$domain_name/dashboard/"
echo "API: https://$domain_name/graphql/"
echo
echo "Make sure to configure a reverse proxy like Nginx for production setups."
echo
