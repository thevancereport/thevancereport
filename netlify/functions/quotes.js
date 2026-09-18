exports.handler = async function(event, context) {

    try {

        const symbol = "WOLF";

        const response = await fetch(
            `https://api.twelvedata.com/quote?symbol=${symbol}&apikey=${process.env.STOCK_API_KEY}`
        );


        const data = await response.json();


        return {
            statusCode: 200,
            body: JSON.stringify(data)
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
