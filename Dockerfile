# Based on PHP 8
FROM php:8-cli-buster

# Set maintainer
LABEL maintainer="Alexander Graf <alex@otherguy.io>"

# Change workdir
WORKDIR /src/

# Required to prevent warnings
ARG DEBIAN_FRONTEND=noninteractive
ARG DEBCONF_NONINTERACTIVE_SEEN=true

# Install dependencies and configure user
RUN apt-get update \
 && apt-get install -y --no-install-recommends --fix-missing \
    libzip-dev \
    unzip

# Install necessary extensions
RUN docker-php-ext-install pdo pdo_mysql mysqli zip

# Install composer
RUN php -r "readfile('http://getcomposer.org/installer');" | php -- --install-dir=/usr/bin/ --filename=composer

# Copy files
COPY . .

# Install dependencies
RUN composer install --no-interaction

# Build arguments
ARG VCS_REF=main \
    BUILD_DATE="" \
    VERSION="${VCS_REF}"

# http://label-schema.org/rc1/
LABEL org.label-schema.schema-version "1.0"
LABEL org.label-schema.name           "nextcloud-cleanup"
LABEL org.label-schema.vendor         "otherguy"
LABEL org.label-schema.version        "${VERSION}"
LABEL org.label-schema.build-date     "${BUILD_DATE}"
LABEL org.label-schema.description    "Cleans up files on Nextcloud S3 storage that are left over from canceled uploads."
LABEL org.label-schema.vcs-url        "https://github.com/otherguy/nextcloud-cleanup"
LABEL org.label-schema.vcs-ref        "${VCS_REF}"

# Expose environment variables to app
ENV VCS_REF="${VCS_REF}" \
    BUILD_DATE="${BUILD_DATE}" \
    VERSION="${VERSION}"

# Entrypoint and Command
ENTRYPOINT ["php"]
CMD ["clean.php"]
