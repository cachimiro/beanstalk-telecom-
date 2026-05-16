FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Export the built files as a named volume target
FROM alpine:3.19
COPY --from=builder /app/dist /dist
CMD ["sh", "-c", "cp -r /dist/* /output/"]
