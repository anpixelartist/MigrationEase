// Command bridge is the TallyMigration local agent CLI.
//
//	bridge version
//	bridge probe [--tally URL]                          verify the local Tally gateway
//	bridge run --relay wss://.../bridge/ws --key bk_... connect to the cloud relay and serve push jobs
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"time"

	"github.com/tallymigration/bridge/internal/tally"
	"github.com/tallymigration/bridge/internal/wsclient"
)

const version = "0.1.0"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "version":
		fmt.Println("tallymigration-bridge", version)
	case "probe":
		os.Exit(probe(os.Args[2:]))
	case "run":
		os.Exit(run(os.Args[2:]))
	default:
		usage()
		os.Exit(2)
	}
}

func probe(args []string) int {
	url := tally.DefaultBaseURL
	for i := 0; i < len(args); i++ {
		if args[i] == "--tally" && i+1 < len(args) {
			url = args[i+1]
			i++
		}
	}
	client := tally.New(url)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	body, err := client.Probe(ctx)
	if err != nil {
		fmt.Printf("Tally gateway NOT reachable at %s: %v\n", url, err)
		return 1
	}
	snippet := strings.TrimSpace(string(body))
	if len(snippet) > 300 {
		snippet = snippet[:300]
	}
	fmt.Printf("Tally gateway reachable at %s\nResponse (first 300 chars):\n%s\n", url, snippet)
	return 0
}

func run(args []string) int {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	relay := fs.String("relay", "", "relay WSS URL, e.g. wss://host/bridge/ws")
	key := fs.String("key", "", "bridge API key (bk_...)")
	tallyURL := fs.String("tally", tally.DefaultBaseURL, "Tally gateway URL")
	company := fs.String("company", "", "active company name (for heartbeats)")
	_ = fs.Parse(args)

	if *relay == "" || *key == "" {
		fmt.Println("run requires --relay and --key")
		return 2
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	fmt.Printf("bridge: connecting to %s (Tally at %s)\n", *relay, *tallyURL)
	cfg := wsclient.Config{RelayURL: *relay, APIKey: *key, Tally: tally.New(*tallyURL), Company: *company}
	if err := wsclient.Run(ctx, cfg); err != nil && ctx.Err() == nil {
		fmt.Println("bridge: stopped with error:", err)
		return 1
	}
	fmt.Println("bridge: shut down")
	return 0
}

func usage() {
	fmt.Println("usage: bridge <command>")
	fmt.Println("  version")
	fmt.Println("  probe [--tally URL]")
	fmt.Println("  run --relay wss://.../bridge/ws --key bk_... [--tally URL] [--company NAME]")
}
