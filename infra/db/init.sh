#!/bin/sh
# Runs once, on first start of the postgres container (docker-entrypoint-initdb.d).
# Creates one database per mail domain, applies the schema and seeds demo users.
# MAIL_USERS format: "email:password email:password ..." (demo credentials only).
set -eu

for domain in $MAIL_DOMAINS; do
    db=$(echo "$domain" | cut -d. -f1)
    echo "initialising database '$db' for $domain"
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -c "CREATE DATABASE \"$db\""
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" -f /schema/schema.sql
    # psql only interpolates :'vars' in SQL read from stdin, not with -c.
    echo "INSERT INTO mua (name) VALUES (:'mua')" |
        psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" -v mua="$TRUSTED_MUA"
    for entry in $MAIL_USERS; do
        email=${entry%%:*}
        case "$email" in *"@$domain") ;; *) continue ;; esac
        echo "INSERT INTO users (email, password_hash) VALUES (:'email', crypt(:'pw', gen_salt('bf')))" |
            psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" -v email="$email" -v pw="${entry#*:}"
    done
done
