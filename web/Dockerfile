FROM node:24-alpine AS dependencies
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY web ./
ARG NEXT_PUBLIC_CONTROL_API_URL=/
ENV NEXT_PUBLIC_CONTROL_API_URL=$NEXT_PUBLIC_CONTROL_API_URL
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
