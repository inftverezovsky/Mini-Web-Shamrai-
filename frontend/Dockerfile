# Build Stage
FROM node:20-alpine AS build

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and build
COPY . .
ARG VITE_API_URL
ARG VITE_ENABLE_DEBUG_AUTH=false
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_ENABLE_DEBUG_AUTH=${VITE_ENABLE_DEBUG_AUTH}
RUN npm run build

# Nginx Stage for production deployment
FROM nginx:stable-alpine

COPY --from=build /app/dist /usr/share/nginx/html

# Custom nginx config to handle React SPA router redirects
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
