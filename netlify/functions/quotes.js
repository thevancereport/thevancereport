exports.handler = async function(event, context) {

    try {

        const symbols = [
            "BMEA",
            "SEDG",
            "PAGS",
            "RUN",
            "LUMN",
            "PLUG",
            "WOLF",
            "FSRN",
            "UPST",
            "CHWY"
        ];

        const results = [];

        for (const symbol of symbols) {

            const response = await fetch(
                `https://api.twelvedata.com/quote?symbol=${symbol}&apikey=${process.env.STOCK_API_KEY}`
            );

            const data = await response.json();

            results.push({
                symbol: symbol,
                price: Number(data.close),
                previousClose: Number(data.previous_close),
                change: Number(data.change),
                percentChange: Number(data.percent_change)
            });
        }


        return {
            statusCode: 200,
            body: JSON.stringify(results)
        };


    } catch (error) {

        return {
            statusCode: 500,
            body: JSON.stringify({
                error: error.message
            })
        };

    }

};
