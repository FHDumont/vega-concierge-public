FROM node:24-slim
WORKDIR /app
ARG NEXT_PUBLIC_VEGA_VERSION=dev
ARG NEXT_PUBLIC_VEGA_GIT_SHA=local
ENV NEXT_PUBLIC_VEGA_VERSION=${NEXT_PUBLIC_VEGA_VERSION}
ENV NEXT_PUBLIC_VEGA_GIT_SHA=${NEXT_PUBLIC_VEGA_GIT_SHA}
# Lockfile commitado → `npm ci` instala EXATAMENTE as versões do lock (CI = local) — F-036.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
