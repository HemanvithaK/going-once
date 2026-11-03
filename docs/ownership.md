# Who owns what

The rule: a service writes only the data it owns, and reads other services only
through the topics listed below. No service queries another service's tables.

## Data

| Data                                           | Owner (only writer) | Visible to others                         |
|------------------------------------------------|---------------------|-------------------------------------------|
| users, refresh tokens, listing metadata        | core api            | no                                        |
| reserve price                                  | core api            | bidding only, via listing-events          |
| orders                                         | core api            | no                                        |
| auction state: price, leader, status, deadline | bidding             | public part via auction-updates           |
| bids, bidding outbox                           | bidding             | no                                        |
| listing's current price and deadline (a copy)  | core api            | public; kept current from auction-updates |
| WebSocket connections, hub state               | realtime            | none; it has no database                  |

The core api's copy of price and deadline is a read model. Only the feed consumer
writes it, and only from auction-updates. The api never decides a price itself.

Orders belong to the core api because an order is a record of a sale, which the api
learns about from auction-outcomes. Bidding decides who won; it does not know about orders.

## Topics

| Topic             | Producer | Consumer(s)                    | Audience                         |
|-------------------|----------|--------------------------------|----------------------------------|
| listing-events    | core api | bidding                        | private (contains reserve price) |
| auction-updates   | bidding  | core api (listing copy)        | public (any viewer)              |
| auction-outcomes  | bidding  | core api (status, order)       | private (names the winner)       |
| listing-snapshots | core api | realtime (today: api consumer) | public (no reserve, no winner)   |

listing-snapshots is temporary. It carries the core api's view of a listing to
WebSocket viewers. When realtime reads auction-updates directly, this topic goes away.

## Event types

| Event             | Topic             | Meaning                                       |
|-------------------|-------------------|-----------------------------------------------|
| listing.published | listing-events    | seller published; bidding starts tracking     |
| auction.updated   | auction-updates   | the public state changed (price, status)      |
| auction.settled   | auction-outcomes  | the auction closed: sold or unsold            |
| listing.updated   | listing-snapshots | the api's view of a listing, for viewers      |

## Why the topics are split

Realtime is the one consumer that talks to browsers. If it could read the outcomes
topic, a bug in the WebSocket code could send a winner's identity to every viewer.
Keeping the topics separate means realtime cannot receive private data at all.

## Trade-offs we know about

- Bidding checks the api's access tokens with a shared signing key and has no users
  table, so it cannot tell that a user was deactivated. A token stays valid for its
  lifetime (up to 15 minutes).
- Processes deliver at least once. Every consumer is written so that applying the same
  message twice leaves the same state.
