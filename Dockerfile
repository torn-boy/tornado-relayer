FROM node:16
WORKDIR /app

COPY package.json yarn.lock .npmrc ./
RUN yarn install --frozen-lockfile && yarn cache clean --force
COPY . .

EXPOSE 8000
ENTRYPOINT ["yarn"]
