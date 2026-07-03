# --- Build Stage ---
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Copy dependency files
COPY go.mod ./
# Copy source code and web assets
COPY main.go ./
COPY dist ./dist

# Generate go.sum and download dependencies
RUN go mod tidy && go mod download


# Build static binary
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o sync-api .

# --- Final Run Stage ---
FROM alpine:3.18

RUN apk --no-cache add ca-certificates tzdata

WORKDIR /root/

# Copy compiled binary from builder
COPY --from=builder /app/sync-api .

# Expose API port
EXPOSE 8080

# Run binary
CMD ["./sync-api"]
